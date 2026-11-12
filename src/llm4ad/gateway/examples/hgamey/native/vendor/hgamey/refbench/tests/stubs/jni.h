#pragma once
// Declarations for host syntax only: not an Android ABI implementation.
using jobject = void *;
using jclass = void *;
using jstring = void *;
using jmethodID = void *;
#define JNI_OK 0
struct JNIEnv {
  jclass GetObjectClass(jobject);
  jmethodID GetMethodID(jclass, const char *, const char *);
  jobject CallObjectMethod(jobject, jmethodID, ...);
  jstring NewStringUTF(const char *);
  void DeleteLocalRef(jobject);
  const char *GetStringUTFChars(jstring, void *);
  void ReleaseStringUTFChars(jstring, const char *);
};
struct JavaVM {
  int AttachCurrentThread(JNIEnv **, void *);
  int DetachCurrentThread();
};
