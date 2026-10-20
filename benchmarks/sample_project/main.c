#include <stdio.h>
#include "project.h"

int main(void) {
    print_banner("PreCompile Sample Benchmark");
    int val = compute_factorial(5);
    printf("Result of 5! = %d\n", val);
    return 0;
}
