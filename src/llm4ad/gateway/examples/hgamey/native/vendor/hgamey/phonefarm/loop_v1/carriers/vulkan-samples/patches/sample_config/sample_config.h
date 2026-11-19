/* Copyright (c) 2026, phonefarm loop_v1 carrier glue
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 the "License";
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

#include "platform/plugins/plugin_base.h"

namespace plugins
{
using SampleConfigTags = vkb::PluginBase<vkb::tags::Passive>;

/**
 * @brief Sample Config
 *
 * Pin a sample to one of the configurations it registers through
 * vkb::Configuration (the same ones batch mode cycles through), so that a
 * single headless run has a *fixed*, reproducible knob state instead of
 * needing the ImGui toggles or a timed batch rotation.
 *
 * Index is 0-based and follows the registration order in the sample's
 * constructor, e.g. for render_passes:
 *   --config 0  ->  loadOp LOAD  + storeOp STORE      (the expensive arm)
 *   --config 1  ->  loadOp CLEAR + storeOp DONT_CARE  (the tile-friendly arm)
 *
 * Usage: vulkan_samples sample render_passes --config 1 --stop-after-frame 1200
 */
class SampleConfig : public SampleConfigTags
{
  public:
	SampleConfig();

	virtual ~SampleConfig() = default;

	bool handle_option(std::deque<std::string> &arguments) override;

	void on_app_start(const std::string &app_id) override;

  private:
	bool     requested{false};
	uint32_t index{0};
};
}        // namespace plugins
