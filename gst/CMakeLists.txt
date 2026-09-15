# SPDX-License-Identifier: Apache-2.0
# The vgacap GStreamer plugin. Optional: skipped entirely when the GStreamer
# development packages are absent, so the core build never depends on them.
find_package(PkgConfig QUIET)
if(NOT PkgConfig_FOUND)
  message(STATUS "vgacap: pkg-config not found, skipping the GStreamer plugin")
  return()
endif()

pkg_check_modules(GST QUIET gstreamer-1.0>=1.16 gstreamer-base-1.0 gstreamer-video-1.0)
if(NOT GST_FOUND)
  message(STATUS "vgacap: GStreamer development files not found, skipping the plugin")
  return()
endif()
message(STATUS "vgacap: building the GStreamer plugin against ${GST_gstreamer-1.0_VERSION}")

# Only -Wconversion is dropped, and only because it fires twice inside
# GStreamer's own headers, where there is no call site to fix:
#   gst/video/video-info.h:374   via GST_VIDEO_INFO_SIZE
#   gst/gstminiobject.h:126      via GST_BUFFER_FLAG_UNSET
# -Werror and -Wshadow stay, so this directory keeps the same regression
# guard as the rest of the tree; -Wall -Wextra -Werror -Wshadow is clean.
get_directory_property(VGACAP_GST_OPTS COMPILE_OPTIONS)
list(REMOVE_ITEM VGACAP_GST_OPTS -Wconversion)
set_directory_properties(PROPERTIES COMPILE_OPTIONS "${VGACAP_GST_OPTS}")

add_library(gstvgacap MODULE gstvgacap.c gstvgadecode.c)
set_target_properties(gstvgacap PROPERTIES
  PREFIX "lib"                       # GStreamer expects libgst<name>.so
  OUTPUT_NAME gstvgacap
  C_STANDARD 99
  C_EXTENSIONS ON                    # GStreamer headers need the GNU extensions
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
target_compile_definitions(gstvgacap PRIVATE
  PACKAGE="vgacap" VGACAP_VERSION="${PROJECT_VERSION}")
target_include_directories(gstvgacap PRIVATE ${GST_INCLUDE_DIRS} ${CMAKE_SOURCE_DIR}/include)
target_compile_options(gstvgacap PRIVATE ${GST_CFLAGS_OTHER})
target_link_directories(gstvgacap PRIVATE ${GST_LIBRARY_DIRS})
target_link_libraries(gstvgacap PRIVATE ${GST_LIBRARIES} vgacap_stream vgacap_frame)

# vgacap_stream/vgacap_frame are static and land inside a shared object.
set_property(TARGET vgacap_stream PROPERTY POSITION_INDEPENDENT_CODE ON)
set_property(TARGET vgacap_frame PROPERTY POSITION_INDEPENDENT_CODE ON)

# gst-inspect-1.0 finds it with GST_PLUGIN_PATH=<build dir>.
add_test(NAME gst_plugin_inspect
  COMMAND ${CMAKE_COMMAND} -E env GST_PLUGIN_PATH=${CMAKE_BINARY_DIR}
          gst-inspect-1.0 vgadecode)
