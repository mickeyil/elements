#include <cstdio>
#include "writh.h"

int arr[5] = {33,2111,1,55,403};

int main()
{
    int n = sizeof(arr) / sizeof(arr[0]);
    printf("array size: %d\n", n);
    for (int i = 0; i < n; i++) {
        printf("%d ", arr[i]);
    }
    printf("\n");
    int m = median(arr, n);
    printf("median: %d\n", m);
    return 0;
}
