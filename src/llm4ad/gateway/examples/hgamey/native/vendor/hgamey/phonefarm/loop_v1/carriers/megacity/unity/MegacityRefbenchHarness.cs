// MegacityRefbenchHarness.cs
//
// 把 megacity-metro 变成一个确定性可复跑的渲染负载。
//
// 放到上游工程的 Assets/Scripts/Refbench/ 下即可, 不改上游任何既有文件。
// 不带 --es scene 启动时本文件完全不激活, APK 就是原版游戏。
//
// 设计边界(与 refbench 一致): 本文件只负责"制造完全一样的渲染负载"并如实记录事实,
// 不测帧时、不算离散度、不做任何判定。测量归 loop_v1 的 raw ftrace 链路。
//
// 状态: 首轮已在红魔(Adreno 840 / Vulkan)上跑通 —— clean_exit=true, 600/600 帧,
// 路线 sha256 与仓库一致, render_thread_comm 实测为 UnityGfxDeviceW。
// 首轮同时暴露了四个问题, 本版针对它们改: 见下面「首轮实跑发现」。
//
// 首轮实跑发现(2026-09-23):
//   1. 城市没渲染出来。根因不是 UGS 卡住 —— Main.unity 里 6 个 SubScene 全是
//      AutoLoadScene=1, 城市会自动装, 不需要玩家驱动。问题是装完要时间, 而当时
//      预热只有 300 帧, 还没装完就开始测了。本版改用"实体数不再增长"作装完信号,
//      而不是拍脑袋等 N 帧。
//   2. 游戏 HUD 与 UGS 报错弹窗盖在画面上, 污染负载。本版进场后关掉所有 Canvas
//      与 UIDocument。
//   3. 进程不自退: Application.Quit() + Environment.Exit() 都杀不掉 Unity 的
//      Android 进程, 写完 json 后 30 秒仍在。本版改用 android.os.Process.killProcess。
//   4. adb push 进 app 外部目录的路线, 应用读不到(scoped storage 下属主是 shell)。
//      本版去掉这条路径, 路线只从 APK 内的 StreamingAssets 读 —— 这反而更好:
//      APK 加上游 commit 就完整决定了负载。换路线要重新出包, 而这正是应该的。

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;

namespace Megacity.Refbench
{
    public sealed class MegacityRefbenchHarness : MonoBehaviour
    {
        const int    ContractVersion = 1;
        const string HarnessVersion  = "0.3.0-unlocked";
        const string Carrier         = "megacity";

        // 与 contract/launch.json 对齐
        const string StartedFileName = "megacity_started";
        const string OutputFileName  = "megacity_out.json";

        // ---- 入口 --------------------------------------------------------
        // AfterSceneLoad: 等第一个场景起来再接管, 免得和上游的 bootstrap 抢时序。
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        static void Boot()
        {
            var scene = GetIntentExtra("scene");
            if (string.IsNullOrEmpty(scene))
                return;   // 正常游玩, 不激活

            var go = new GameObject("[MegacityRefbenchHarness]");
            DontDestroyOnLoad(go);
            go.AddComponent<MegacityRefbenchHarness>();
        }

        // ---- 参数 --------------------------------------------------------
        string m_Scene, m_RunId, m_RouteName, m_VSync, m_UnityScene;
        int    m_Frames, m_Warmup, m_Settle;
        float  m_ResScale;

        // ---- 帧率上限与路线推进方式 (审计 #8, 2026-10-21) ----
        // 旧版两个分支都写 targetFrameRate=-1, 在 Android 上 -1 就是 30 fps, 实测 fps_mean 29.3-29.7,
        // 「帧率不锁」不成立。现在 vsync=off 时 vSyncCount=0 + targetFrameRate=fps_cap(缺省 1000),
        // 实际上限由屏幕刷新率 / 系统帧调度决定, 回读值和窗口内观察到的帧率写进 json 的 frame_pacing。
        //
        // 解锁后帧率随候选变化, 而 phonefarm 的采集窗是 marker 之后固定秒数 (game_opt_loop 里
        // Megacity seconds=30)。相机若仍按帧序号推进, 快的一臂在同样 30 秒里会飞得更远, 两臂量的
        // 不是同一段路线。所以缺省改为按时间推进 (pace=time): u = 窗口内已过时间 / route_seconds,
        // 同一时刻两臂相机在同一位置, 固定秒数的采集窗覆盖的是同一段路线。
        // route_seconds 缺省 = frames / 30, 与旧版在 30 fps 下的飞行速度完全相同, 旧的 extras 不用改。
        // pace=frame 保留旧行为 (逐帧位姿完全相同), 只在需要复现旧数据时用。
        const float LegacyPaceFps = 30f;
        string m_Pace;
        float  m_RouteSeconds;
        int    m_FpsCap;
        int    m_RequestedTargetFps;
        double m_WarmupSeconds, m_WindowSeconds;

        // ---- 白盒旋钮 (阶段 3, https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/MEGACITY_WHITEBOX_KNOBS.md W1–W7) ----
        // 全部可选; 不给就不碰。生效结果逐条写进 json 的 "knobs": 想要的值、怎么改的
        // (prop / field / none / failed:...)、改完回读的实际值。改不动就如实记, 绝不静默降级。
        string m_KMsaa, m_KStoreOpt, m_KFeatOff, m_KFogEvent, m_KSsaoSrc, m_KSsaoAfterOpaque, m_KShadowRes, m_KDepthPriming;
        readonly List<string> m_KnobLog = new List<string>();

        // 静置阶段的事实, 全部如实回写进 json
        int    m_SettleFrames;
        long   m_Entities = -1;
        string m_StreamingReady;
        int    m_UiDisabled;
        string m_ModeSet = "none";

        // 实体数连续这么多帧不变, 就认为 subscene 装完了
        const int StableFrames = 120;

        Route  m_Route;
        Camera m_Cam;

        int    m_WarmupRendered;
        int    m_Rendered;
        bool   m_CleanExit;
        string m_AbortReason = "";

        void Awake()
        {
            m_Scene     = GetIntentExtra("scene");
            m_RunId     = GetIntentExtra("run_id")   ?? "unset";
            m_RouteName = GetIntentExtra("route")    ?? "route_a";
            m_VSync     = GetIntentExtra("vsync")    ?? "off";
            // 上游启动进的是 Menu 场景, 城市在 Main 里。不切过去就只是在菜单前面摆了台相机。
            m_UnityScene = GetIntentExtra("unity_scene") ?? "Main";
            m_Frames    = ParseInt(GetIntentExtra("frames"),  3600, 60, 100000);
            m_Warmup    = ParseInt(GetIntentExtra("warmup"),   600,  0, 100000);
            // settle 是"等城市装完"的上界, 不是固定等待 —— 装完就提前走
            m_Settle    = ParseInt(GetIntentExtra("settle"),  7200, 0, 200000);
            m_ResScale  = ParseFloat(GetIntentExtra("resscale"), 1.0f, 0.25f, 2.0f);
            m_Pace      = GetIntentExtra("pace") == "frame" ? "frame" : "time";
            m_RouteSeconds = ParseFloat(GetIntentExtra("route_seconds"), m_Frames / LegacyPaceFps, 1f, 100000f);
            m_FpsCap    = ParseInt(GetIntentExtra("fps_cap"), 1000, 1, 1000);
            m_KMsaa            = GetIntentExtra("msaa");               // W1: 1 / 2 / 4
            m_KStoreOpt        = GetIntentExtra("store_opt");          // W2: auto / discard / store
            m_KFeatOff         = GetIntentExtra("feat_off");           // W3: 逗号分隔的渲染器特性名 (子串匹配, 不分大小写)
            m_KFogEvent        = GetIntentExtra("fog_event");          // W4: RenderPassEvent 名或整数
            m_KSsaoSrc         = GetIntentExtra("ssao_src");           // W5: depth / depthnormals
            m_KSsaoAfterOpaque = GetIntentExtra("ssao_after_opaque");  // W5: 0 / 1
            m_KShadowRes       = GetIntentExtra("shadowres");          // W6: 256..4096
            m_KDepthPriming    = GetIntentExtra("depth_priming");      // W7: off / auto / forced
        }

        IEnumerator Start()
        {
            // 1) 把一切会让两轮不一样的自适应行为按死
            //    帧率不锁 -> 分位数统计能在固定采集窗内拿到更多帧(与 refbench 同理)。
            //    注意 Android 上 targetFrameRate=-1 等于 30 fps (旧版就栽在这里), 必须给一个大数。
            //    移动端忽略 vSyncCount, 真正的上限是屏幕刷新率, 回读写进 json 的 frame_pacing。
            if (m_VSync == "on") { QualitySettings.vSyncCount = 1; m_RequestedTargetFps = -1; }
            else                 { QualitySettings.vSyncCount = 0; m_RequestedTargetFps = m_FpsCap; }
            Application.targetFrameRate = m_RequestedTargetFps;

            Screen.sleepTimeout = SleepTimeout.NeverSleep;
            // 交通/AI 若用了 UnityEngine.Random, 固定种子消掉这一路的轮间差异。
            // (ECS 侧若另有 Random 实例, 这里管不到 —— 属于已知残留风险, 见 README)
            UnityEngine.Random.InitState(20260923);

            SetRenderScale(m_ResScale);

            // 2) 路线: 缺了就响亮地失败, 绝不退回默认相机
            yield return LoadRoute(m_RouteName, r => m_Route = r);
            if (m_Route == null || m_Route.Points.Count < 2)
            {
                Abort(string.IsNullOrEmpty(m_AbortReason) ? "route_invalid" : m_AbortReason);
                yield break;
            }

            // 3) 切到城市场景。上游默认启动的是 Menu, 城市资产在 Main 及其 subscene 里。
            if (SceneManager.GetActiveScene().name != m_UnityScene)
            {
                AsyncOperation load = null;
                try { load = SceneManager.LoadSceneAsync(m_UnityScene, LoadSceneMode.Single); }
                catch (Exception e) { m_AbortReason = "scene_load_failed:" + e.GetType().Name; }
                if (load == null) { Abort("scene_load_failed"); yield break; }
                while (!load.isDone) yield return null;
            }

            // 4) 切单机模式。两个作用: 上游在非单机时才实例化 Vivox, 而 Vivox 会弹
            //    录音权限对话框 —— 对话框一出应用就被挂起, 协程根本不跑。
            m_ModeSet = SetSinglePlayerMode();

            // 5) 静置: 等城市真的装完。
            //    Main.unity 的 6 个 SubScene 都是 AutoLoadScene=1, 不需要玩家驱动,
            //    但装完要时间。信号用"实体数连续 StableFrames 帧不再增长", 而不是
            //    固定等 N 帧 —— 固定等待在慢的轮上会漏, 在快的轮上会白等。
            //    拿不到实体数(反射失败)时退回走满 settle, 并在 json 里如实标 unknown。
            long lastCount = -1;
            int  stable = 0;
            for (m_SettleFrames = 0; m_SettleFrames < m_Settle; m_SettleFrames++)
            {
                yield return new WaitForEndOfFrame();
                if (!Application.isFocused) { Abort("focus_lost"); yield break; }

                // HUD 和 UGS 弹窗会盖在画面上, 既挡视野又进渲染负载。UI 可能晚于
                // 场景出现, 所以整个静置期间反复关。
                m_UiDisabled += DisableGameUI();
                if (m_SettleFrames % 30 == 0) SuppressOtherCameras();

                long n = TryGetEntityCount();
                if (n < 0) { m_StreamingReady = "unknown"; continue; }
                m_Entities = n;
                if (n == lastCount)
                {
                    if (++stable >= StableFrames) { m_StreamingReady = "yes"; break; }
                }
                else { stable = 0; lastCount = n; }
            }
            if (m_StreamingReady == null) m_StreamingReady = "timeout";

            // 5.5) 白盒旋钮: 城市装完、预热开始前一次性生效 (预热期间让 shader / 渲染器重建落定)
            //      参数写错 (比如 msaa=x) 不能让整轮崩掉: 记成 failed, 本轮照跑, 上层看 knobs 判废
            try { ApplyKnobs(); }
            catch (Exception e) { LogKnob("apply", "all", "failed:" + e.GetType().Name + ":" + e.Message, null); }

            // 6) 接管相机
            m_Cam = TakeOverCamera();
            if (m_Cam == null) { Abort("no_camera"); yield break; }

            // 7) 预热: 钉在路线起点渲染 warmup 帧, 等 shader 与常驻分配落定。
            //    预热的帧数/耗时顺带给出实际帧率上限 (只作核对上限用, 不是测量, 帧时仍归 ftrace)。
            ApplyPose(0f);
            double w0 = Time.realtimeSinceStartupAsDouble;
            for (int i = 0; i < m_Warmup; i++)
            {
                yield return new WaitForEndOfFrame();
                if (!Application.isFocused) { Abort("focus_lost"); yield break; }
                ApplyPose(0f);
                m_WarmupRendered++;
            }
            m_WarmupSeconds = Time.realtimeSinceStartupAsDouble - w0;
            Debug.Log("[refbench] frame_pacing " + FramePacingJson());

            // 8) 落 started 标记 —— 采集从这里开始算
            WriteStartedMarker();

            // 9) 被测窗口。
            //    pace=time (缺省): 相机位姿只由窗口内已过的真实时间决定, 跑满 route_seconds 秒。
            //      phonefarm 在 marker 之后采固定秒数, 两臂帧率不同时, 同一时刻相机仍在同一位置,
            //      采集窗覆盖同一段路线。帧数随帧率变, 如实记 frames_rendered。
            //    pace=frame (旧行为): 位姿只由帧序号决定, 跑满 frames 帧。逐帧位姿完全相同,
            //      但帧率不锁时固定秒数的采集窗会在两臂覆盖不同长度的路线, 只用于复现旧数据。
            double t0 = Time.realtimeSinceStartupAsDouble;
            for (int i = 0; ; i++)
            {
                double el = Time.realtimeSinceStartupAsDouble - t0;
                if (m_Pace == "frame" ? i >= m_Frames : el >= m_RouteSeconds) break;
                yield return new WaitForEndOfFrame();
                if (!Application.isFocused) { m_WindowSeconds = Time.realtimeSinceStartupAsDouble - t0; Abort("focus_lost"); yield break; }

                float u;
                if (m_Scene == "city_static") u = 0f;
                else if (m_Pace == "frame")   u = m_Frames <= 1 ? 0f : (float)i / (m_Frames - 1);
                else                          u = (float)((Time.realtimeSinceStartupAsDouble - t0) / m_RouteSeconds);
                ApplyPose(u);
                m_Rendered++;
            }
            m_WindowSeconds = Time.realtimeSinceStartupAsDouble - t0;

            m_CleanExit = true;
            Finish();
        }

        // ---- 相机 --------------------------------------------------------

        // 上游的相机被 ECS/玩家输入驱动, 留着它就没有确定性可言。玩家实体可能在
        // 静置期间才生成并带出新相机, 所以这件事要反复做, 不能只做一次。
        static void SuppressOtherCameras()
        {
            foreach (var c in Camera.allCameras)
                if (c != null && c.gameObject.name != "[RefbenchCamera]") c.enabled = false;
        }

        Camera TakeOverCamera()
        {
            SuppressOtherCameras();

            var go = new GameObject("[RefbenchCamera]");
            DontDestroyOnLoad(go);
            var cam = go.AddComponent<Camera>();
            cam.fieldOfView = m_Route.Fov > 0f ? m_Route.Fov : 60f;
            cam.nearClipPlane = 0.1f;
            cam.farClipPlane  = m_Route.Far > 0f ? m_Route.Far : 3000f;
            cam.depth = 100;
            // URP 的逐相机附加数据由 URP 在 OnEnable 时补齐, 这里不手动构造,
            // 免得和不同 URP 版本的 UniversalAdditionalCameraData 绑死。
            return cam;
        }

        // 帧率上限的事实: 要的值、回读值、屏幕刷新率、预热与窗口里观察到的帧率。
        // 观察帧率 = 帧数 / 真实时间, 只用来核对「上限到底是多少」(例: 仍是 30 就说明没解锁),
        // 不进任何判定; 判定用的帧时仍由 loop_v1 从 raw ftrace 算。
        string FramePacingJson()
        {
            var ci = CultureInfo.InvariantCulture;
            double hz = -1;
            try { hz = Screen.currentResolution.refreshRateRatio.value; } catch { }
            string F(double x) => (double.IsNaN(x) || double.IsInfinity(x)) ? "null" : x.ToString("0.###", ci);
            var sb = new StringBuilder(256);
            sb.Append('{');
            JS(sb, "pace", m_Pace); sb.Append(',');
            sb.Append("\"route_seconds\":").Append(F(m_RouteSeconds)).Append(',');
            J(sb, "requested_target_frame_rate", m_RequestedTargetFps); sb.Append(',');
            J(sb, "target_frame_rate", Application.targetFrameRate); sb.Append(',');
            J(sb, "vsync_count", QualitySettings.vSyncCount); sb.Append(',');
            sb.Append("\"display_refresh_hz\":").Append(F(hz)).Append(',');
            sb.Append("\"warmup_seconds\":").Append(F(m_WarmupSeconds)).Append(',');
            sb.Append("\"warmup_observed_fps\":").Append(m_WarmupSeconds > 0 ? F(m_WarmupRendered / m_WarmupSeconds) : "null").Append(',');
            sb.Append("\"window_seconds\":").Append(F(m_WindowSeconds)).Append(',');
            sb.Append("\"window_observed_fps\":").Append(m_WindowSeconds > 0 ? F(m_Rendered / m_WindowSeconds) : "null");
            sb.Append('}');
            return sb.ToString();
        }

        void ApplyPose(float u)
        {
            m_Route.Sample(u, out var pos, out var rot);
            m_Cam.transform.SetPositionAndRotation(pos, rot);
        }

        static void SetRenderScale(float s)
        {
            // 反射取 URP asset 的 renderScale, 避免对 UniversalRenderPipelineAsset 的编译期依赖
            // (上游 URP 版本变动时这里不会炸编译, 最多是不生效 —— 生效与否回写进 json 由上层核对)
            var rp = GraphicsSettings.currentRenderPipeline;
            if (rp == null) return;
            var p = rp.GetType().GetProperty("renderScale");
            if (p != null && p.CanWrite) p.SetValue(rp, s);
        }

        static float GetRenderScale()
        {
            var rp = GraphicsSettings.currentRenderPipeline;
            var p = rp?.GetType().GetProperty("renderScale");
            return p != null ? Convert.ToSingle(p.GetValue(rp)) : -1f;
        }

        // ---- 白盒旋钮实现 (全部反射, 不对 URP 类型做编译期依赖) -------------

        const BindingFlags AnyInst = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        // 先试属性 (含非公开 setter), 再试字段; 返回改法。值按目标类型转换 (枚举接受名字或整数)。
        static string SetMember(object o, string prop, string field, object v)
        {
            if (o == null) return "none:null_target";
            try
            {
                var t = o.GetType();
                var p = prop != null ? t.GetProperty(prop, AnyInst) : null;
                if (p != null && p.GetSetMethod(true) != null) { p.SetValue(o, Coerce(v, p.PropertyType)); return "prop"; }
                for (var tt = t; tt != null; tt = tt.BaseType)
                {
                    var f = field != null ? tt.GetField(field, AnyInst | BindingFlags.DeclaredOnly) : null;
                    if (f != null) { f.SetValue(o, Coerce(v, f.FieldType)); return "field"; }
                }
                return "none";
            }
            catch (Exception e) { return "failed:" + (e.InnerException ?? e).GetType().Name; }
        }

        static object GetMember(object o, string prop, string field)
        {
            if (o == null) return null;
            try
            {
                var t = o.GetType();
                var p = prop != null ? t.GetProperty(prop, AnyInst) : null;
                if (p != null && p.GetGetMethod(true) != null) return p.GetValue(o);
                for (var tt = t; tt != null; tt = tt.BaseType)
                {
                    var f = field != null ? tt.GetField(field, AnyInst | BindingFlags.DeclaredOnly) : null;
                    if (f != null) return f.GetValue(o);
                }
            }
            catch { }
            return null;
        }

        static object Coerce(object v, Type t)
        {
            if (t.IsEnum)
            {
                var s = Convert.ToString(v, CultureInfo.InvariantCulture);
                if (int.TryParse(s, out var n)) return Enum.ToObject(t, n);
                foreach (var name in Enum.GetNames(t))
                    if (string.Equals(name, s, StringComparison.OrdinalIgnoreCase)) return Enum.Parse(t, name);
                throw new ArgumentException("enum " + t.Name + " 没有 " + s);
            }
            if (t == typeof(bool)) return Convert.ToString(v, CultureInfo.InvariantCulture) is var b && (b == "1" || string.Equals(b, "true", StringComparison.OrdinalIgnoreCase));
            return Convert.ChangeType(v, t, CultureInfo.InvariantCulture);
        }

        void LogKnob(string name, string want, string how, object actual)
        {
            m_KnobLog.Add("\"" + Esc(name) + "\":{\"want\":\"" + Esc(want) + "\",\"how\":\"" + Esc(how)
                          + "\",\"actual\":\"" + Esc(Convert.ToString(actual, CultureInfo.InvariantCulture) ?? "null") + "\"}");
        }

        static IEnumerable<ScriptableObject> RendererDatas(object asset)
        {
            var list = GetMember(asset, "rendererDataList", "m_RendererDataList") as System.Collections.IEnumerable;
            if (list == null) yield break;
            foreach (var d in list) if (d is ScriptableObject so && so != null) yield return so;
        }

        static IEnumerable<ScriptableObject> Features(ScriptableObject rd)
        {
            var list = GetMember(rd, "rendererFeatures", "m_RendererFeatures") as System.Collections.IEnumerable;
            if (list == null) yield break;
            foreach (var f in list) if (f is ScriptableObject so && so != null) yield return so;
        }

        static bool NameHas(ScriptableObject f, string key)
            => f.name.IndexOf(key, StringComparison.OrdinalIgnoreCase) >= 0
               || f.GetType().Name.IndexOf(key, StringComparison.OrdinalIgnoreCase) >= 0;

        static void MarkDirty(ScriptableObject rd)
        {
            // 渲染器数据改了要让 URP 重建渲染器; SetDirty 在不同 URP 版本可见性不同, 反射调
            try { rd.GetType().GetMethod("SetDirty", AnyInst)?.Invoke(rd, null); } catch { }
        }

        void ApplyKnobs()
        {
            var asset = GraphicsSettings.currentRenderPipeline;
            if (asset == null)
            {
                if (m_KMsaa != null || m_KStoreOpt != null || m_KFeatOff != null || m_KFogEvent != null || m_KSsaoSrc != null
                    || m_KSsaoAfterOpaque != null || m_KShadowRes != null || m_KDepthPriming != null)
                    LogKnob("all", "requested", "none:no_srp_asset", null);
                return;
            }
            // W1 MSAA
            if (m_KMsaa != null)
                LogKnob("msaa", m_KMsaa, SetMember(asset, "msaaSampleCount", "m_MSAA", int.Parse(m_KMsaa, CultureInfo.InvariantCulture)),
                        GetMember(asset, "msaaSampleCount", "m_MSAA"));
            // W2 store actions: auto / discard / store
            if (m_KStoreOpt != null)
                LogKnob("store_opt", m_KStoreOpt, SetMember(asset, "storeActionsOptimization", "m_StoreActionsOptimization", m_KStoreOpt),
                        GetMember(asset, "storeActionsOptimization", "m_StoreActionsOptimization"));
            // W6 阴影分辨率
            if (m_KShadowRes != null)
                LogKnob("shadowres", m_KShadowRes, SetMember(asset, "mainLightShadowmapResolution", "m_MainLightShadowmapResolution", int.Parse(m_KShadowRes, CultureInfo.InvariantCulture)),
                        GetMember(asset, "mainLightShadowmapResolution", "m_MainLightShadowmapResolution"));

            foreach (var rd in RendererDatas(asset))
            {
                bool dirty = false;
                // W7 depth priming: off / auto / forced (= Disabled / Auto / Forced)
                if (m_KDepthPriming != null)
                {
                    var v = m_KDepthPriming == "off" ? "Disabled" : m_KDepthPriming;
                    LogKnob("depth_priming@" + rd.name, m_KDepthPriming, SetMember(rd, "depthPrimingMode", "m_DepthPrimingMode", v),
                            GetMember(rd, "depthPrimingMode", "m_DepthPrimingMode"));
                    dirty = true;
                }
                foreach (var f in Features(rd))
                {
                    // W3 关特性
                    if (m_KFeatOff != null)
                        foreach (var key in m_KFeatOff.Split(','))
                            if (key.Trim().Length > 0 && NameHas(f, key.Trim()))
                            {
                                string how;
                                try { f.GetType().GetMethod("SetActive", AnyInst)?.Invoke(f, new object[] { false }); how = "SetActive"; }
                                catch (Exception e) { how = "failed:" + e.GetType().Name; }
                                LogKnob("feat_off:" + f.name, "off", how, GetMember(f, "isActive", "m_Active"));
                                dirty = true;
                            }
                    // W4 雾的注入时机: 特性本身或其一级字段里第一个 RenderPassEvent 类型的成员
                    if (m_KFogEvent != null && NameHas(f, "fog"))
                    {
                        string how = "none:no_RenderPassEvent_member";
                        object actual = null;
                        foreach (var fi in f.GetType().GetFields(AnyInst))
                        {
                            if (fi.FieldType.Name == "RenderPassEvent") { how = SetMember(f, null, fi.Name, m_KFogEvent); actual = fi.GetValue(f); break; }
                            var inner = fi.FieldType.IsClass && fi.FieldType != typeof(string) ? fi.GetValue(f) : null;
                            if (inner == null) continue;
                            foreach (var fj in inner.GetType().GetFields(AnyInst))
                                if (fj.FieldType.Name == "RenderPassEvent") { how = SetMember(inner, null, fj.Name, m_KFogEvent) + "@" + fi.Name; actual = fj.GetValue(inner); break; }
                            if (actual != null) break;
                        }
                        LogKnob("fog_event:" + f.name, m_KFogEvent, how, actual);
                        dirty = true;
                    }
                    // W5 SSAO 设置
                    if ((m_KSsaoSrc != null || m_KSsaoAfterOpaque != null) && NameHas(f, "AmbientOcclusion"))
                    {
                        var st = GetMember(f, null, "m_Settings");
                        if (m_KSsaoSrc != null)
                            LogKnob("ssao_src:" + f.name, m_KSsaoSrc,
                                    SetMember(st, null, "Source", m_KSsaoSrc == "depth" ? "Depth" : "DepthNormals"), GetMember(st, null, "Source"));
                        if (m_KSsaoAfterOpaque != null)
                            LogKnob("ssao_after_opaque:" + f.name, m_KSsaoAfterOpaque,
                                    SetMember(st, null, "AfterOpaque", m_KSsaoAfterOpaque), GetMember(st, null, "AfterOpaque"));
                        dirty = true;
                    }
                }
                if (dirty) MarkDirty(rd);
            }
            if (m_KFeatOff != null && !m_KnobLog.Exists(x => x.StartsWith("\"feat_off:")))
                LogKnob("feat_off", m_KFeatOff, "none:no_feature_matched", null);
            if (m_KFogEvent != null && !m_KnobLog.Exists(x => x.StartsWith("\"fog_event:")))
                LogKnob("fog_event", m_KFogEvent, "none:no_fog_feature", null);
            if ((m_KSsaoSrc != null || m_KSsaoAfterOpaque != null) && !m_KnobLog.Exists(x => x.StartsWith("\"ssao_")))
                LogKnob("ssao", (m_KSsaoSrc ?? "") + "/" + (m_KSsaoAfterOpaque ?? ""), "none:no_ssao_feature", null);
        }

        // ---- 上游状态的三处干预 ------------------------------------------
        // 全部走反射: 这些类型在上游的 asmdef 程序集里, 直接引用会把本文件和上游的
        // 程序集结构绑死, 上游一改就炸编译。反射拿不到就如实记 none, 不假装成功。

        // 单机模式。上游 SpawnSinglePlayer 只在这个开关为真时生成玩家; 更关键的是
        // PlayerInfoController.Start() 只在非单机时实例化 Vivox, 而 Vivox 会弹录音
        // 权限框 —— 框一出应用就挂起, 协程停摆。首轮就是栽在这里。
        static string SetSinglePlayerMode()
        {
            try
            {
                // 权威开关是 ScriptableObject 上的字段, 控制器只是转发
                foreach (var so in Resources.FindObjectsOfTypeAll<ScriptableObject>())
                {
                    if (so == null || so.GetType().Name != "PlayerInfoItemSettings") continue;
                    var f = so.GetType().GetField("GameMode");
                    if (f == null || !f.FieldType.IsEnum) continue;
                    f.SetValue(so, Enum.Parse(f.FieldType, "SinglePlayer"));
                    return "settings";
                }
            }
            catch (Exception e) { return "failed:" + e.GetType().Name; }
            return "none";
        }

        // HUD、UGS 报错弹窗这些既挡视野又进渲染负载, 一律关掉。
        static int DisableGameUI()
        {
            int n = 0;
            try
            {
                foreach (var c in FindObjectsByType<Canvas>(FindObjectsInactive.Include, FindObjectsSortMode.None))
                    if (c != null && c.enabled) { c.enabled = false; n++; }
                foreach (var b in FindObjectsByType<MonoBehaviour>(FindObjectsInactive.Include, FindObjectsSortMode.None))
                    if (b != null && b.enabled && b.GetType().Name == "UIDocument") { b.enabled = false; n++; }
            }
            catch { /* 关不掉就算了, 数量会体现在 json 里 */ }
            return n;
        }

        // 实体总数。用它的"停止增长"作 subscene 装完的信号。
        // 注意: 这是装载状态, 不是性能指标 —— 载体不做任何测量, 这条边界没破。
        static long TryGetEntityCount()
        {
            try
            {
                var worldType = Type.GetType("Unity.Entities.World, Unity.Entities");
                var world = worldType?.GetProperty("DefaultGameObjectInjectionWorld",
                                BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
                if (world == null) return -1;
                var em = worldType.GetProperty("EntityManager")?.GetValue(world);
                var dbg = em?.GetType().GetProperty("Debug")?.GetValue(em);
                var cnt = dbg?.GetType().GetProperty("EntityCount")?.GetValue(dbg);
                return cnt == null ? -1 : Convert.ToInt64(cnt);
            }
            catch { return -1; }
        }

        // ---- 路线 --------------------------------------------------------

        sealed class Route
        {
            public string Name = "";
            public string Sha256 = "";
            public float  Fov, Far;
            public readonly List<Vector3> Points = new();
            public readonly List<Quaternion> Rots = new();

            // Catmull-Rom, 端点各自复制一份做哨兵。参数 u 属于 [0,1]。
            public void Sample(float u, out Vector3 pos, out Quaternion rot)
            {
                int n = Points.Count;
                u = Mathf.Clamp01(u);
                float f = u * (n - 1);
                int   i = Mathf.Min((int)f, n - 2);
                float t = f - i;

                Vector3 p0 = Points[Mathf.Max(i - 1, 0)];
                Vector3 p1 = Points[i];
                Vector3 p2 = Points[i + 1];
                Vector3 p3 = Points[Mathf.Min(i + 2, n - 1)];

                pos = 0.5f * ((2f * p1) + (-p0 + p2) * t
                    + (2f * p0 - 5f * p1 + 4f * p2 - p3) * t * t
                    + (-p0 + 3f * p1 - 3f * p2 + p3) * t * t * t);

                rot = Quaternion.Slerp(Rots[i], Rots[i + 1], t);
            }
        }

        // 路线只从 APK 内的 StreamingAssets 读。
        // 首轮试过把路线 adb push 进 app 的外部 files 目录让它免重新出包就能换,
        // 实测读不到: scoped storage 下 push 进去的文件属主是 shell, 应用看不见
        // (chown 也没救回来)。所以这条路径整个去掉 —— 留着只会让人以为能用。
        // 副作用反而是好的: APK + 上游 commit 完整决定了负载, 换路线必须重新出包,
        // 而路线本来就是被测配置的一部分, 本就该这样。
        IEnumerator LoadRoute(string name, Action<Route> done)
        {
            var url = Path.Combine(Application.streamingAssetsPath, "refbench_routes", name + ".json");
            using var req = UnityWebRequest.Get(url);
            yield return req.SendWebRequest();
            if (req.result != UnityWebRequest.Result.Success)
            {
                m_AbortReason = "route_missing";
                done(null);
                yield break;
            }
            done(ParseRoute(name, req.downloadHandler.data));
        }

        Route ParseRoute(string name, byte[] raw)
        {
            try
            {
                var dto = JsonUtility.FromJson<RouteDto>(Encoding.UTF8.GetString(raw));
                if (dto?.waypoints == null || dto.waypoints.Length < 2)
                {
                    m_AbortReason = "route_invalid:too_few_waypoints";
                    return null;
                }

                var r = new Route { Name = name, Fov = dto.fov, Far = dto.far };
                foreach (var w in dto.waypoints)
                {
                    r.Points.Add(new Vector3(w.pos[0], w.pos[1], w.pos[2]));
                    r.Rots.Add(Quaternion.Euler(w.rot[0], w.rot[1], w.rot[2]));
                }
                using var sha = SHA256.Create();
                r.Sha256 = ToHex(sha.ComputeHash(raw));
                return r;
            }
            catch (Exception e)
            {
                m_AbortReason = "route_invalid:" + e.GetType().Name;
                return null;
            }
        }

        [Serializable] class RouteDto { public float fov; public float far; public WaypointDto[] waypoints; }
        [Serializable] class WaypointDto { public float[] pos; public float[] rot; }

        // ---- 产出 --------------------------------------------------------

        void WriteStartedMarker()
        {
            var p = Path.Combine(Application.persistentDataPath, StartedFileName);
            WriteAndSync(p, Encoding.UTF8.GetBytes(m_Scene + " " + m_RunId));
        }

        void Abort(string reason)
        {
            m_CleanExit = false;
            m_AbortReason = string.IsNullOrEmpty(m_AbortReason) ? reason : m_AbortReason;
            Finish();
        }

        void Finish()
        {
            try { WriteAndSync(Path.Combine(Application.persistentDataPath, OutputFileName),
                               Encoding.UTF8.GetBytes(BuildJson())); }
            catch (Exception e) { Debug.LogError("[refbench] 输出写盘失败: " + e); }

            KillSelf();
        }

        // 首轮实测: Application.Quit() 和 Environment.Exit() 都杀不掉 Unity 的
        // Android 进程 —— json 写完 30 秒后进程仍在, 上层的存活探测会一直空等。
        // 直接走 Android 的进程终止。json 在此之前已经 fsync 落盘, 杀掉是安全的。
        static void KillSelf()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using var proc = new AndroidJavaClass("android.os.Process");
                proc.CallStatic("killProcess", proc.CallStatic<int>("myPid"));
                return;
            }
            catch { /* 落到下面的兜底 */ }
#endif
            Application.Quit();
        }

        static void WriteAndSync(string path, byte[] bytes)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None);
            fs.Write(bytes, 0, bytes.Length);
            fs.Flush(true);   // true = 落到存储介质, 不只是 OS 缓冲
        }

        string BuildJson()
        {
            var ci = CultureInfo.InvariantCulture;
            var sb = new StringBuilder(2048);
            sb.Append('{');
            J(sb, "contract_version", ContractVersion); sb.Append(',');
            JS(sb, "carrier", Carrier); sb.Append(',');
            JS(sb, "app_version", Application.version); sb.Append(',');
            JS(sb, "harness_version", HarnessVersion); sb.Append(',');
            JS(sb, "upstream_commit", UpstreamCommit.Value); sb.Append(',');
            JS(sb, "run_id", m_RunId); sb.Append(',');
            JS(sb, "scene", m_Scene); sb.Append(',');

            sb.Append("\"params\":{");
            J(sb, "frames", m_Frames); sb.Append(',');
            J(sb, "warmup", m_Warmup); sb.Append(',');
            J(sb, "settle", m_Settle); sb.Append(',');
            JS(sb, "pace", m_Pace ?? ""); sb.Append(',');
            sb.Append("\"route_seconds\":").Append(m_RouteSeconds.ToString("R", ci)).Append(',');
            J(sb, "fps_cap", m_FpsCap); sb.Append(',');
            sb.Append("\"resscale\":").Append(m_ResScale.ToString("R", ci)).Append(',');
            JS(sb, "msaa", m_KMsaa ?? ""); sb.Append(',');
            JS(sb, "store_opt", m_KStoreOpt ?? ""); sb.Append(',');
            JS(sb, "feat_off", m_KFeatOff ?? ""); sb.Append(',');
            JS(sb, "fog_event", m_KFogEvent ?? ""); sb.Append(',');
            JS(sb, "ssao_src", m_KSsaoSrc ?? ""); sb.Append(',');
            JS(sb, "ssao_after_opaque", m_KSsaoAfterOpaque ?? ""); sb.Append(',');
            JS(sb, "shadowres", m_KShadowRes ?? ""); sb.Append(',');
            JS(sb, "depth_priming", m_KDepthPriming ?? ""); sb.Append(',');
            JS(sb, "vsync", m_VSync);
            sb.Append("},");

            sb.Append("\"route\":{");
            JS(sb, "name", m_Route?.Name ?? m_RouteName); sb.Append(',');
            JS(sb, "sha256", m_Route?.Sha256 ?? ""); sb.Append(',');
            J(sb, "waypoints", m_Route?.Points.Count ?? 0);
            sb.Append("},");

            sb.Append("\"graphics\":{");
            JS(sb, "api", SystemInfo.graphicsDeviceType.ToString()); sb.Append(',');
            JS(sb, "device_name", SystemInfo.graphicsDeviceName); sb.Append(',');
            JS(sb, "device_version", SystemInfo.graphicsDeviceVersion); sb.Append(',');
            J(sb, "width", Screen.width); sb.Append(',');
            J(sb, "height", Screen.height); sb.Append(',');
            sb.Append("\"render_scale\":").Append(GetRenderScale().ToString("R", ci)).Append(',');
            J(sb, "vsync_count", QualitySettings.vSyncCount); sb.Append(',');
            J(sb, "target_frame_rate", Application.targetFrameRate); sb.Append(',');
            JS(sb, "quality_level", QualitySettings.names[QualitySettings.GetQualityLevel()]); sb.Append(',');
            JS(sb, "srp_asset", GraphicsSettings.currentRenderPipeline != null
                                ? GraphicsSettings.currentRenderPipeline.name : "none");
            sb.Append(',');
            // 回读: 不管有没有给旋钮, 都把关键设置的实际值写出来, 便于核对两臂真的不同
            var rpa = GraphicsSettings.currentRenderPipeline;
            JS(sb, "msaa", Convert.ToString(GetMember(rpa, "msaaSampleCount", "m_MSAA"), ci) ?? "null"); sb.Append(',');
            JS(sb, "store_opt", Convert.ToString(GetMember(rpa, "storeActionsOptimization", "m_StoreActionsOptimization"), ci) ?? "null"); sb.Append(',');
            JS(sb, "shadowres", Convert.ToString(GetMember(rpa, "mainLightShadowmapResolution", "m_MainLightShadowmapResolution"), ci) ?? "null"); sb.Append(',');
            sb.Append("\"renderer_features\":[");
            bool firstF = true;
            if (rpa != null)
                foreach (var rd in RendererDatas(rpa))
                    foreach (var f in Features(rd))
                    {
                        if (!firstF) sb.Append(',');
                        firstF = false;
                        sb.Append("{\"renderer\":\"").Append(Esc(rd.name)).Append("\",\"name\":\"").Append(Esc(f.name))
                          .Append("\",\"type\":\"").Append(Esc(f.GetType().Name)).Append("\",\"active\":\"")
                          .Append(Esc(Convert.ToString(GetMember(f, "isActive", "m_Active"), ci))).Append("\"}");
                    }
            sb.Append(']');
            sb.Append("},");
            sb.Append("\"knobs\":{").Append(string.Join(",", m_KnobLog)).Append("},");

            JS(sb, "render_thread_comm", DetectGfxThreadComm()); sb.Append(',');

            // 静置阶段的事实。streaming_ready != "yes" 的轮不能当基线用 ——
            // 城市没装完就开测, 量到的不是这座城。
            sb.Append("\"streaming\":{");
            JS(sb, "ready", m_StreamingReady ?? "not_reached"); sb.Append(',');
            J(sb, "settle_frames", m_SettleFrames); sb.Append(',');
            sb.Append("\"entities\":").Append(m_Entities).Append(',');
            J(sb, "ui_disabled", m_UiDisabled); sb.Append(',');
            JS(sb, "mode_set", m_ModeSet);
            sb.Append("},");

            sb.Append("\"frame_pacing\":").Append(FramePacingJson()).Append(',');
            J(sb, "frames_rendered", m_Rendered); sb.Append(',');
            J(sb, "warmup_frames_rendered", m_WarmupRendered); sb.Append(',');
            sb.Append("\"clean_exit\":").Append(m_CleanExit ? "true" : "false");
            if (!m_CleanExit) { sb.Append(','); JS(sb, "abort_reason", m_AbortReason); }
            sb.Append('}');
            return sb.ToString();
        }

        // parse_trace.py --comm 要的就是这个名字。不靠猜, 进程内自己扫一遍。
        static string DetectGfxThreadComm()
        {
            try
            {
                var found = new List<string>();
                foreach (var d in Directory.GetDirectories("/proc/self/task"))
                {
                    var f = Path.Combine(d, "comm");
                    if (!File.Exists(f)) continue;
                    var c = File.ReadAllText(f).Trim();
                    if (c.IndexOf("Gfx", StringComparison.OrdinalIgnoreCase) >= 0) return c;
                    found.Add(c);
                }
                // 没匹配到就把全部线程名交出来供人工定位, 不静默返回一个猜的值
                return "UNRESOLVED[" + string.Join("|", found) + "]";
            }
            catch (Exception e) { return "UNRESOLVED:" + e.GetType().Name; }
        }

        static void J (StringBuilder sb, string k, int v)    => sb.Append('"').Append(k).Append("\":").Append(v);
        static void JS(StringBuilder sb, string k, string v) => sb.Append('"').Append(k).Append("\":\"").Append(Esc(v)).Append('"');
        static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
        static string ToHex(byte[] b) { var sb = new StringBuilder(b.Length * 2); foreach (var x in b) sb.Append(x.ToString("x2")); return sb.ToString(); }

        // ---- intent extras ----------------------------------------------

        static string GetIntentExtra(string key)
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            try
            {
                using var up     = new AndroidJavaClass("com.unity3d.player.UnityPlayer");
                using var act    = up.GetStatic<AndroidJavaObject>("currentActivity");
                using var intent = act.Call<AndroidJavaObject>("getIntent");
                var v = intent.Call<string>("getStringExtra", key);
                return string.IsNullOrEmpty(v) ? null : v;
            }
            catch { return null; }
#else
            return null;
#endif
        }

        static int ParseInt(string s, int def, int lo, int hi)
            => int.TryParse(s, NumberStyles.Integer, CultureInfo.InvariantCulture, out var v)
               ? Mathf.Clamp(v, lo, hi) : def;

        static float ParseFloat(string s, float def, float lo, float hi)
            => float.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var v)
               ? Mathf.Clamp(v, lo, hi) : def;
    }
}
