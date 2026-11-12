#pragma once
#include <android/native_activity.h>
#include <android/window.h>
struct android_app {
  ANativeActivity *activity;
  ANativeWindow *window;
  int destroyRequested;
  void (*onAppCmd)(android_app *, int32_t);
};
struct android_poll_source {
  void (*process)(android_app *, android_poll_source *);
};
extern "C" int ALooper_pollOnce(int, int *, int *, void **);
#define APP_CMD_INIT_WINDOW 1
#define APP_CMD_TERM_WINDOW 2
