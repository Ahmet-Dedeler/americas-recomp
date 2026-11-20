#pragma once
#include <windows.h>
typedef DWORD pthread_key_t;
inline int pthread_key_create(pthread_key_t* k, void (*d)(void*)) { DWORD i = FlsAlloc((PFLS_CALLBACK_FUNCTION)d); if (i == FLS_OUT_OF_INDEXES) return 1; *k = i; return 0; }
inline void* pthread_getspecific(pthread_key_t k) { return FlsGetValue(k); }
inline int pthread_setspecific(pthread_key_t k, const void* v) { return FlsSetValue(k, (PVOID)v) ? 0 : 1; }
