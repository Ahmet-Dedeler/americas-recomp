#include <atomic>
extern "C" bool LibcRunReportEnabled_nid_no_patch() noexcept { return false; }
extern "C" void LibcRunReportImageFormat_nid_no_patch(unsigned, const char*, int, int) noexcept {}
extern "C" void LibcRunReportSetFormatCounters_nid_no_patch(const std::atomic<unsigned long long>*, unsigned) noexcept {}
