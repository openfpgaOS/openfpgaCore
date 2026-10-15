#include <initializer_list>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
int main() {
  uint64_t pointer_checks=0,address_checks=0;
  for (auto a:{6u,12u}) {
    uint32_t mask=(1u<<a)-1,free_mask=(1u<<(a+1))-1;
    for(auto q:{1u,4u,a}) {
      uint32_t capacity=(1u<<q)-1;
      for(uint32_t w=0;w<=mask;w++)for(uint32_t r=0;r<=mask;r++) {
        uint32_t used=(w-r)&mask,free=(capacity-used)&free_mask;
        bool old_full=free==0,new_full=r==((w-capacity)&mask);
        if(old_full!=new_full)std::abort();pointer_checks++;
      }
    }
    for(uint32_t r=0;r<=mask;r++)for(uint32_t take=0;take<2;take++) {
      if(((r+take)&mask)!=(take?((r+1)&mask):r))std::abort();address_checks++;
    }
  }
  // Production queue has 12-bit monotonic pointers and nine physical bits.
  for(uint32_t w=0;w<4096;w++)for(uint32_t r=0;r<4096;r++) {
    if((((511-((w-r)&4095))&8191)==0)!=(r==((w-511)&4095)))std::abort();pointer_checks++;
  }
  std::printf("PASS exhaustive %llu full-width pointer pairs (including invalid occupancies/wrap), %llu lookahead combinations\n",(unsigned long long)pointer_checks,(unsigned long long)address_checks);
}
