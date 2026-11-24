// SPDX-License-Identifier: Apache-2.0
// USB descriptors: one CDC ACM serial port.
//
// Raspberry Pi's VID with the PID pico-sdk uses for an RP2350 CDC device
// (pico_stdio_usb), and our own strings, so the port shows up as
// /dev/serial/by-id/usb-vgacap_vgacap_capture_firmware_<board id>-if00 --
// distinct from the stock MicroPython firmware's "Board in FS mode".
#include <string.h>

#include "pico/unique_id.h"
#include "tusb.h"

#define USBD_VID 0x2E8A
#define USBD_PID 0x0009
#define USBD_ITF_CDC 0
#define USBD_ITF_MAX 2
#define USBD_CDC_EP_CMD 0x81
#define USBD_CDC_EP_OUT 0x02
#define USBD_CDC_EP_IN 0x82
#define USBD_CDC_CMD_MAX_SIZE 8
#define USBD_CDC_IN_OUT_MAX_SIZE 64
#define USBD_DESC_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)

enum { STR_LANGID, STR_MANUFACTURER, STR_PRODUCT, STR_SERIAL, STR_CDC };

static const tusb_desc_device_t device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USBD_VID,
    .idProduct = USBD_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = STR_MANUFACTURER,
    .iProduct = STR_PRODUCT,
    .iSerialNumber = STR_SERIAL,
    .bNumConfigurations = 1,
};

static const uint8_t configuration[USBD_DESC_LEN] = {
    TUD_CONFIG_DESCRIPTOR(1, USBD_ITF_MAX, 0, USBD_DESC_LEN, 0, 250),
    TUD_CDC_DESCRIPTOR(USBD_ITF_CDC, STR_CDC, USBD_CDC_EP_CMD, USBD_CDC_CMD_MAX_SIZE, USBD_CDC_EP_OUT,
                       USBD_CDC_EP_IN, USBD_CDC_IN_OUT_MAX_SIZE),
};

const uint8_t *tud_descriptor_device_cb(void) { return (const uint8_t *)&device; }

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return configuration;
}

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    static uint16_t desc[40];
    static char serial[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
    const char *text;
    switch (index) {
    case STR_LANGID:
        desc[1] = 0x0409;
        desc[0] = (uint16_t)((TUSB_DESC_STRING << 8) | 4u);
        return desc;
    case STR_MANUFACTURER: text = "vgacap"; break;
    case STR_PRODUCT: text = "vgacap capture firmware"; break;
    case STR_SERIAL:
        pico_get_unique_board_id_string(serial, sizeof serial);
        text = serial;
        break;
    case STR_CDC: text = "vgacap capture"; break;
    default: return NULL;
    }
    size_t len = strlen(text);
    if (len > 39) len = 39;
    for (size_t i = 0; i < len; i++) desc[1 + i] = (uint8_t)text[i];
    desc[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2u * len + 2u));
    return desc;
}
