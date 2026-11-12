#pragma once
#include <cstdint>
#include <pthread.h>
#ifdef __APPLE__
inline int refbench_host_thread_name(pthread_t, const char *name) {
  return pthread_setname_np(name);
}
#define pthread_setname_np(thread, name) refbench_host_thread_name(thread, name)
#endif
