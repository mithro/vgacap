# SPDX-License-Identifier: Apache-2.0
# vgacap-rp1: build on a Raspberry Pi 5 against raspberrypi/utils' piolib.
#
#   git clone --depth 1 https://github.com/raspberrypi/utils.git
#   make -C capture-rp1 PIOLIB=$PWD/utils/piolib
#
# piolib is compiled in (three C files, BSD-3-Clause) rather than linked, so
# no libpio-dev package is needed and 32- and 64-bit userlands both work.

PIOLIB ?= ../../utils/piolib
CFLAGS ?= -O2 -Wall -Wextra
VGACAP := ..

vgacap-rp1: vgacap_rp1.c $(VGACAP)/src/stream/writer.c
	$(CC) $(CFLAGS) -DLIBRARY_BUILD=1 -I$(PIOLIB)/include -I$(VGACAP)/include \
	    -o $@ vgacap_rp1.c $(VGACAP)/src/stream/writer.c \
	    $(PIOLIB)/piolib.c $(PIOLIB)/library_piochips.c $(PIOLIB)/pio_rp1.c -lpthread

clean:
	rm -f vgacap-rp1

.PHONY: clean
