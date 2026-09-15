// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
TEST(smoke_passes) { ASSERT_EQ_U(1 + 1, 2); }
int main(void) { RUN(smoke_passes); RUN_TESTS_END(); }
