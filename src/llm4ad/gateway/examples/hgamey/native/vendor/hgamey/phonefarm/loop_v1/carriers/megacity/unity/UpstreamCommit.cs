// UpstreamCommit.cs
//
// 构建时由 MegacityRefbenchBuild.StampUpstreamCommit() 重写, 把 megacity-metro
// 的 git commit 钉进包里 —— 证据链要能回答"这个 APK 是哪份源码出的"。
//
// 仓库里这一份是默认值, 保证不跑构建脚本时工程也能编译。

namespace Megacity.Refbench
{
    public static class UpstreamCommit
    {
        public const string Value = "UNSET";
    }
}
