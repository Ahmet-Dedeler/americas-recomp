// stands in for a GDK title: reaches the runtime the way the static library does (QueryApiImpl + vtable slots)
#include <windows.h>
#include <cstdio>
#include <cstdint>
typedef HRESULT (*Q)(const GUID&, const GUID&, void**);
struct AsyncBlock { void* queue; void* context; void* callback; unsigned char internal[32]; };
static void** Get(Q q, GUID g){ void* o=0; HRESULT hr=q(g,g,&o); if(hr) {printf("query failed %08lx\n",hr); exit(1);} return *(void***)o; }
int main(){
  HMODULE m=LoadLibraryA("xgameruntime.dll"); Q q=(Q)GetProcAddress(m,"QueryApiImpl");
  GUID user={0x01acd177,0x91f9,0x4763,{0xa3,0x8e,0xcc,0xbb,0x55,0xce,0x32,0xe0}};
  GUID thr ={0x073b7dcb,0x1fcf,0x4030,{0x94,0xbe,0xe3,0xc9,0xeb,0x62,0x34,0x28}};
  void* uo=0; q(user,user,&uo); void** uv=*(void***)uo;
  void* to=0; q(thr,thr,&to); void** tv=*(void***)to;
  AsyncBlock ab={}; // no queue: the process default queue is used
  HRESULT hr=((HRESULT(*)(void*,uint32_t,AsyncBlock*))uv[7])(uo,1,&ab);
  printf("XUserAddAsync hr=%08lx\n",hr);
  hr=((HRESULT(*)(void*,AsyncBlock*,BOOLEAN))tv[3])(to,&ab,TRUE);
  printf("XAsyncGetStatus(wait) hr=%08lx\n",hr);
  void* h=0; hr=((HRESULT(*)(void*,AsyncBlock*,void**))uv[8])(uo,&ab,&h);
  printf("XUserAddResult hr=%08lx handle=%p\n",hr,h);
  uint64_t id=0; hr=((HRESULT(*)(void*,void*,uint64_t*))uv[11])(uo,h,&id); printf("XUserGetId hr=%08lx id=%llu\n",hr,id);
  uint32_t st=9; hr=((HRESULT(*)(void*,void*,uint32_t*))uv[14])(uo,h,&st); printf("XUserGetState hr=%08lx state=%u\n",hr,st);
  BYTE has=0; hr=((HRESULT(*)(void*,void*,uint32_t,uint32_t,BYTE*))uv[20])(uo,h,0,1,&has); printf("XUserCheckPrivilege hr=%08lx has=%d\n",hr,has);
  GUID sysg={0xb884675d,0xb738,0x4a9c,{0x81,0x5d,0x9a,0x9a,0x1e,0x0c,0x6c,0x9b}};
  void* so=0; q(sysg,sysg,&so); void** sv=*(void***)so;
  struct Info{ uint16_t os[4],host[4]; char fam[64],form[64]; } info={};
  ((void*(*)(void*,Info*))sv[3])(so,&info); printf("analytics: %s / %s os %u.%u.%u\n",info.fam,info.form,info.os[0],info.os[1],info.os[2]);
  ((void(*)(void*,void*))uv[4])(uo,h);
  return 0;
}
