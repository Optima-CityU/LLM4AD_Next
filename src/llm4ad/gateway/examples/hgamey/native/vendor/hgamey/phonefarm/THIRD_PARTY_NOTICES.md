# 第三方组件与移植来源声明 (THIRD_PARTY_NOTICES)

本仓库包含移植自下列上游开源项目的功能实现。上游均为宽松许可，允许使用、修改与再分发，
但要求保留版权与许可声明。**这些声明不是可选项**：任何新增的移植文件都必须在代码头部注明
来源，并在此处登记。

---

## 1. SimRun

- 上游: <https://github.com/unixzii/SimRun>
- 许可: MIT License
- 版权: Copyright (c) 2026 Cyandev
- 引入 commit: `e77beff`
- 本仓库移植的部分:
  - agent skill 分发机制（`phonefarm skill`；对应上游 `simrun skill [install]`）
  - 「脚本 + 结构化观测」两段式（对应上游 `simrun eval` + `script.dylib`）——仅借设计，无代码

## 2. FluidUse

- 上游: <https://github.com/FluidInference/FluidUse>
- 许可: Apache License 2.0
- 版权: Copyright FluidInference
- 引入 commit: `9384e70`
- 本仓库移植的部分:
  - **决策原语通路**（`src/decision.rs` / `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_DECISION_PATH.md`）——对应上游的
    typed decisions（`choice` / `score` / `noul`）协议形状，以及桥接层的会话纪律
    （保序 / 一次一个 / 超时即杀 / 杀掉即 closed / 模型侧错误不杀会话 / stderr 尾部）。
    **仅借语义，无代码**：FluidUse 是 Swift + Core ML，本仓库是 Rust。
  - **资产可复现管线**（`src/decision.rs` 的 `verify_manifest`）——对应上游的 pinned
    manifest + SHA-256 + 按需下载。本仓库只做校验，不做下载。
  - **本仓库不随附任何训练好的权重**。Core ML 包只能在 Apple 侧用 Core ML 运行时加载；
    `phonefarm decide --sidecar` 是把它接进来的插座，`tools/decision-sidecar/` 是那颗插座
    的参考实现（Swift + Core ML，含一个 137 字节的参考模型）。真模型得使用者自己拿来。

## 3. iPhone-use

- 上游: <https://github.com/zhongerxin/iPhone-use>
- 许可: MIT License
- 版权: Copyright (c) 2026 ZHONG XIN
- 引入 commit: `08ed5b0`
- 本仓库移植的部分:
  - **iOS 设备后端**（`src/ios.rs`）——对应上游 `server/wda_client.py` / `wda_controller.py` /
    `wda_apps.py`：USB → 本机 loopback 转发 → HTTP → WDA 这条通路，以及
    「WDA 的 JSON source 不带 clickable/scrollable，需按类型推导」这个坑。仅借语义，无代码。
  - **观察预算契约**（`https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_OBSERVATION_BUDGET.md`）——对应上游 `server/wda_client.py`
    的紧凑控件树 / 去重 / 有界滚动 / 截图复用
  - **组合动作原语**（`src/collect.rs`）——对应上游 `pua_scroll_find` 与 `pua_collect_list`
  - **设备屏幕观战窗口**（`src/screen.rs` / `src/screen_widget.html` /
    `https://github.com/HGamey/docs/blob/main/repos/phonefarm/docs/SPEC_SCREEN_WIDGET.md`）——对应上游的实时屏幕 widget（`ui/`）与屏幕流通路
    （`server/wda_screen.py`）：手机外壳、设备状态、操作光效、点击/拖动提示、暂停恢复、
    屏幕流不落盘。仅借语义，无代码。
  - **MCP `structuredContent`**（`src/serve.rs`）——对应上游 `structuredContent` 返回约定
  - 上游自身的依赖（Appium / WebDriverAgent / appium-ios-device）与本仓库无关：
    本仓库不随附 WebDriverAgent，也不负责安装它。`iproxy` / `idevice_id` 来自
    libimobiledevice，由使用者自己装。

---

# 上游许可全文

## MIT License (SimRun)

```
MIT License

Copyright (c) 2026 Cyandev

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## MIT License (iPhone-use)

```
MIT License

Copyright (c) 2026 ZHONG XIN

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Apache License 2.0 (FluidUse)

FluidUse is licensed under the Apache License, Version 2.0. 本仓库引入了它的
**协议与会话纪律语义**（`src/decision.rs`），未复制其源代码。若后续直接复制其源码，
须随附完整许可全文与 NOTICE 文件（如有）。全文见
<https://www.apache.org/licenses/LICENSE-2.0>。
