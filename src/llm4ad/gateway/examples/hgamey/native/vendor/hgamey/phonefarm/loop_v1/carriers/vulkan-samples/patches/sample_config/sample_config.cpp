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

#include "sample_config.h"

#include "vulkan_sample.h"

namespace plugins
{
SampleConfig::SampleConfig() :
    SampleConfigTags("Sample Config",
                     "Pin a sample to a fixed configuration index instead of using the GUI or batch rotation.",
                     {vkb::Hook::OnAppStart},
                     {},
                     {{"config", "Apply the sample configuration with this 0-based index and keep it for the whole run"}})
{
}

bool SampleConfig::handle_option(std::deque<std::string> &arguments)
{
	assert(!arguments.empty() && (arguments[0].substr(0, 2) == "--"));
	std::string option = arguments[0].substr(2);
	if (option == "config")
	{
		if (arguments.size() < 2)
		{
			LOGE("Option \"config\" is missing the configuration index!");
			return false;
		}
		index     = static_cast<uint32_t>(std::stoul(arguments[1]));
		requested = true;

		arguments.pop_front();
		arguments.pop_front();
		return true;
	}
	return false;
}

void SampleConfig::on_app_start(const std::string &app_id)
{
	if (!requested)
	{
		return;
	}

	// Mirrors what batch mode does, minus the timer: walk the sample's own
	// configuration list to the requested index and apply it once. The samples
	// re-read these values every frame (and rebuild render targets when they
	// change), so applying right after prepare() is enough.
	vkb::Configuration *configuration = nullptr;

	if (auto *sample_c = dynamic_cast<vkb::VulkanSampleC *>(&platform->get_app()))
	{
		configuration = &sample_c->get_configuration();
	}
	else if (auto *sample_cpp = dynamic_cast<vkb::VulkanSampleCpp *>(&platform->get_app()))
	{
		configuration = &sample_cpp->get_configuration();
	}

	if (configuration == nullptr)
	{
		LOGE("--config {}: app \"{}\" is not a Vulkan sample, configuration not applied", index, app_id);
		return;
	}

	configuration->reset();
	for (uint32_t i = 0; i < index; ++i)
	{
		if (!configuration->next())
		{
			LOGE("--config {}: sample \"{}\" only registers {} configuration(s), index out of range", index, app_id, i + 1);
			return;
		}
	}
	configuration->set();

	LOGI("sample_config: applied configuration index {} to \"{}\"", index, app_id);
}
}        // namespace plugins
