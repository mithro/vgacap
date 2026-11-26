# SPDX-License-Identifier: Apache-2.0
#
# The `vgacap` module for MicroPython's make-based ports. This is the unix
# test build: the capture engines on the simulated board (firmware/sim),
# which is what python/tests/test_mpy_module.py drives. The board build is
# micropython.cmake (the rp2 port). See README.md.
#
#   make -C ports/unix USER_C_MODULES=<vgacap checkout> ...
#
# (USER_C_MODULES names the directory that *contains* mpy/; MicroPython
# includes every <dir>/*/micropython.mk under it.)

VGACAP_MOD_DIR := $(USERMOD_DIR)
VGACAP_ROOT := $(VGACAP_MOD_DIR)/..

# The binding: scanned for qstrs.
SRC_USERMOD_C += $(VGACAP_MOD_DIR)/modvgacap.c $(VGACAP_MOD_DIR)/hal_mpy.c

# The shared C, compiled but not scanned: the engines, the PIO builder, the
# clock planner, the stream writer, and the simulated board without its own
# host link.
SRC_USERMOD_LIB_C += \
	$(VGACAP_ROOT)/firmware/core/fw_capture.c \
	$(VGACAP_ROOT)/firmware/core/fw_args.c \
	$(VGACAP_ROOT)/firmware/core/fw_board.c \
	$(VGACAP_ROOT)/firmware/core/fw_clock.c \
	$(VGACAP_ROOT)/firmware/core/fw_pio.c \
	$(VGACAP_ROOT)/firmware/sim/hal_sim.c \
	$(VGACAP_ROOT)/src/stream/writer.c

CFLAGS_USERMOD += -I$(VGACAP_MOD_DIR) -I$(VGACAP_ROOT)/firmware/core \
	-I$(VGACAP_ROOT)/firmware/sim -I$(VGACAP_ROOT)/include \
	-DVGACAP_MPY_SIM=1 -DVGACAP_SIM_NO_LINK=1
LDFLAGS_USERMOD += -lm
