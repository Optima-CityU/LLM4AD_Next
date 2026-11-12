#pragma once
// Missing from the installed OH header subset. Minimal used Android surface
// declaration for syntax only; no implementation or Android toolchain claim.
struct ANativeWindow;
typedef VkFlags VkAndroidSurfaceCreateFlagsKHR;
typedef struct VkAndroidSurfaceCreateInfoKHR {
  VkStructureType sType;
  const void *pNext;
  VkAndroidSurfaceCreateFlagsKHR flags;
  ANativeWindow *window;
} VkAndroidSurfaceCreateInfoKHR;
extern "C" VKAPI_ATTR VkResult VKAPI_CALL
vkCreateAndroidSurfaceKHR(VkInstance, const VkAndroidSurfaceCreateInfoKHR *,
                          const VkAllocationCallbacks *, VkSurfaceKHR *);
