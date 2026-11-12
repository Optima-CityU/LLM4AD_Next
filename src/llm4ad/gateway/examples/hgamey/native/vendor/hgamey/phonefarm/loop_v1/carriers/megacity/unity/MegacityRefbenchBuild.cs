// MegacityRefbenchBuild.cs  —  放到上游工程的 Assets/Editor/ 下
//
// 零交互出 arm64 / Vulkan / IL2CPP 的 APK:
//
//   Unity.exe -quit -batchmode -nographics -projectPath <proj> \
//             -executeMethod Megacity.Refbench.MegacityRefbenchBuild.BuildAndroid \
//             -buildOut <abs apk path> -logFile <log>
//
// 原则与 refbench 一致: 设置对不上就让构建失败, 绝不"尽力而为"出一个悄悄退回
// GLES3 或 armv7 的包 —— 那种包测出来的数跟我们以为在测的东西没关系。
//
// 状态: 已实跑出包成功 —— 构建后自检四项(arm64 / Vulkan / IL2CPP / apk)全通过。

#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEngine;
using UnityEngine.Rendering;

namespace Megacity.Refbench
{
    public static class MegacityRefbenchBuild
    {
        const string CityScene = "Assets/Scenes/Main.unity";
        const string MenuScene = "Assets/Scenes/Menu.unity";

        public static void BuildAndroid()
        {
            var outPath = Arg("-buildOut") ?? throw new Exception("缺 -buildOut <apk 绝对路径>");
            var commit  = Arg("-upstreamCommit") ?? "UNSET";

            StampUpstreamCommit(commit);
            ConfigureAndroid();

            var scenes = ResolveScenes();
            Directory.CreateDirectory(Path.GetDirectoryName(outPath));

            var opts = new BuildPlayerOptions
            {
                scenes           = scenes,
                locationPathName = outPath,
                target           = BuildTarget.Android,
                targetGroup      = BuildTargetGroup.Android,
                options          = BuildOptions.None,   // 非 development build: 不带 profiler 开销
            };

            var report = BuildPipeline.BuildPlayer(opts);
            var s = report.summary;
            Debug.Log($"[refbench] 构建结束 result={s.result} size={s.totalSize} errors={s.totalErrors} out={outPath}");

            if (s.result != UnityEditor.Build.Reporting.BuildResult.Succeeded)
            {
                // batchmode 下 -quit 的退出码要靠这里显式给非 0
                EditorApplication.Exit(1);
                return;
            }
            VerifyOrDie();
            EditorApplication.Exit(0);
        }

        // ---- 设置 --------------------------------------------------------

        static void ConfigureAndroid()
        {
            EditorUserBuildSettings.SwitchActiveBuildTarget(NamedBuildTarget.Android, BuildTarget.Android);
            EditorUserBuildSettings.buildAppBundle  = false;   // 要 .apk 不要 .aab
            EditorUserBuildSettings.androidBuildSubtarget = MobileTextureSubtarget.ASTC;
            EditorUserBuildSettings.development     = false;
            EditorUserBuildSettings.allowDebugging  = false;
            EditorUserBuildSettings.connectProfiler = false;

            // 目标: arm64 + Vulkan + IL2CPP。这三条是本载体存在的前提。
            PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
            PlayerSettings.SetGraphicsAPIs(BuildTarget.Android, new[] { GraphicsDeviceType.Vulkan });
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Android, ScriptingImplementation.IL2CPP);
            PlayerSettings.SetIl2CppCompilerConfiguration(NamedBuildTarget.Android, Il2CppCompilerConfiguration.Release);

            // 不要让引擎替我们改分辨率/帧率 —— 自适应是确定性的天敌
            PlayerSettings.Android.optimizedFramePacing = false;
            PlayerSettings.Android.renderOutsideSafeArea = true;
            PlayerSettings.defaultInterfaceOrientation  = UIOrientation.LandscapeLeft;
            PlayerSettings.runInBackground = false;   // 失焦即 abort, 与 harness 的 focus_lost 对齐

            AssetDatabase.SaveAssets();
        }

        static string[] ResolveScenes()
        {
            // harness 用 SceneManager.LoadSceneAsync("Main") 切过去, 所以 Main 必须在构建场景表里。
            var list = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToList();
            foreach (var need in new[] { MenuScene, CityScene })
            {
                if (!File.Exists(need))
                    throw new Exception($"上游场景不在预期路径: {need} —— 上游目录结构变了, 先核对再改这里, 不要猜");
                if (!list.Contains(need)) list.Add(need);
            }
            // Menu 必须是第 0 个: 上游就是从它启动的, 顺序换了正常游玩也会坏
            list.Remove(MenuScene);
            list.Insert(0, MenuScene);
            Debug.Log("[refbench] 构建场景表: " + string.Join(", ", list));
            return list.ToArray();
        }

        static void StampUpstreamCommit(string commit)
        {
            var path = "Assets/Scripts/Refbench/UpstreamCommit.cs";
            if (!File.Exists(path)) { Debug.LogWarning("[refbench] 找不到 " + path + ", 跳过 commit 钉入"); return; }
            File.WriteAllText(path,
                "// 构建脚本生成, 勿手改\n" +
                "namespace Megacity.Refbench\n{\n" +
                "    public static class UpstreamCommit\n    {\n" +
                $"        public const string Value = \"{commit}\";\n" +
                "    }\n}\n");
            AssetDatabase.Refresh();
        }

        // ---- 自检 --------------------------------------------------------
        // 出完包再回读一次设置。构建过程中任何一环把图形 API 或架构改回去了,
        // 在这里炸掉, 而不是等测完一轮才发现测的是 GLES3。
        static void VerifyOrDie()
        {
            var problems = new List<string>();

            var apis = PlayerSettings.GetGraphicsAPIs(BuildTarget.Android);
            if (apis.Length != 1 || apis[0] != GraphicsDeviceType.Vulkan)
                problems.Add("图形 API 不是单一 Vulkan, 实际= " + string.Join("+", apis));

            if (PlayerSettings.Android.targetArchitectures != AndroidArchitecture.ARM64)
                problems.Add("目标架构不是纯 ARM64, 实际= " + PlayerSettings.Android.targetArchitectures);

            if (PlayerSettings.GetScriptingBackend(NamedBuildTarget.Android) != ScriptingImplementation.IL2CPP)
                problems.Add("脚本后端不是 IL2CPP");

            if (EditorUserBuildSettings.buildAppBundle)
                problems.Add("出的是 aab 不是 apk");

            if (problems.Count > 0)
            {
                Debug.LogError("[refbench] 构建后自检不通过:\n  - " + string.Join("\n  - ", problems));
                EditorApplication.Exit(3);
            }
            Debug.Log("[refbench] 构建后自检通过: arm64 / Vulkan / IL2CPP / apk");
        }

        static string Arg(string name)
        {
            var a = Environment.GetCommandLineArgs();
            for (int i = 0; i < a.Length - 1; i++)
                if (a[i] == name) return a[i + 1];
            return null;
        }
    }
}
#endif
