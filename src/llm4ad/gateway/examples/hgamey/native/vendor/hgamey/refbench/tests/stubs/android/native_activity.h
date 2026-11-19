#pragma once
#include <jni.h>
struct ANativeActivity {
  JavaVM *vm;
  jobject clazz;
  const char *internalDataPath;
  const char *externalDataPath;
};
extern "C" void ANativeActivity_finish(ANativeActivity *);
extern "C" void ANativeActivity_setWindowFlags(ANativeActivity *, unsigned int,
                                               unsigned int);
#define AWINDOW_FLAG_KEEP_SCREEN_ON 128
#define AWINDOW_FLAG_FULLSCREEN 1024
