# SPDX-License-Identifier: Apache-2.0
# vgacap-rp1: build on a Raspberry Pi 5 against raspberrypi/utils' piolib.
#
#   git clone --depth 1 https://github.com/raspberrypi/utils.git
#   make -C capture-rp1 PIOLIB=$PWD/utils/piolib
#
# piolib is compiled in (three C files, BSD-3-Clause) rather than linked, so
# no libpio-dev package is needed and 32- and 64-bit userlands both work.
#
# vgacap-rp1-programs prints the PIO programs vgacap-rp1 loads; it needs only
# piolib's headers and builds and runs on any host.

PIOLIB ?= ../../utils/piolib
CFLAGS ?= -O2 -Wall -Wextra
VGACAP := ..

all: vgacap-rp1 vgacap-rp1-programs

vgacap-rp1: vgacap_rp1.c programs.h $(VGACAP)/src/stream/writer.c
	$(CC) $(CFLAGS) -DLIBRARY_BUILD=1 -I$(PIOLIB)/include -I$(VGACAP)/include \
	    -o $@ vgacap_rp1.c $(VGACAP)/src/stream/writer.c \
	    $(PIOLIB)/piolib.c $(PIOLIB)/library_piochips.c $(PIOLIB)/pio_rp1.c -lpthread

vgacap-rp1-programs: print_programs.c programs.h
	$(CC) $(CFLAGS) -I$(PIOLIB)/include -o $@ print_programs.c

clean:
	rm -f vgacap-rp1 vgacap-rp1-programs

.PHONY: all clean
