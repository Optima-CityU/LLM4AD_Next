#pragma once
#include <cstdarg>
#include <cstdio>
#include <string>
#define ANDROID_LOG_INFO 4
inline std::string rk_host_marker;
inline int __android_log_print(int, const char*, const char* format, ...) {
    char b[65536]; va_list args; va_start(args,format);
    int n = vsnprintf(b,sizeof b,format,args); va_end(args);
    if (b[0] == '{') rk_host_marker = b;
    return n;
}
