// Ember & Tide dial helpers (LVGL 8/9 safe). Included by .embertide-dial-common.yaml.
#pragma once
#include <string>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace et {

inline void show(lv_obj_t *o, bool on) {
  if (on) {
#if LVGL_VERSION_MAJOR >= 9
    lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
#else
    lv_obj_clear_flag(o, LV_OBJ_FLAG_HIDDEN);
#endif
  } else {
    lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
  }
}

inline void poke() {
#if LVGL_VERSION_MAJOR >= 9
  lv_display_trigger_activity(NULL);
#else
  lv_disp_trig_activity(NULL);
#endif
}

inline void text(lv_obj_t *o, const std::string &s) { lv_label_set_text(o, s.c_str()); }
inline void color(lv_obj_t *o, uint32_t c) { lv_obj_set_style_text_color(o, lv_color_hex(c), LV_PART_MAIN); }
inline void border(lv_obj_t *o, uint32_t c) { lv_obj_set_style_border_color(o, lv_color_hex(c), LV_PART_MAIN); }
inline void bg(lv_obj_t *o, uint32_t c) { lv_obj_set_style_bg_color(o, lv_color_hex(c), LV_PART_MAIN); }

inline bool usable(const std::string &s) {
  return !s.empty() && s != "unknown" && s != "unavailable" && s != "None";
}

// "#rrggbb" from Ember & Tide -> 0xRRGGBB; anything else -> fallback.
inline uint32_t hexcol(const std::string &s, uint32_t fb) {
  if (s.size() != 7 || s[0] != '#') return fb;
  char *end = nullptr;
  unsigned long v = strtoul(s.c_str() + 1, &end, 16);
  return (end && *end == 0) ? (uint32_t) v : fb;
}
inline uint32_t pcol(int p, const std::string &c0, const std::string &c1, uint32_t f0, uint32_t f1) {
  return p == 0 ? hexcol(c0, f0) : hexcol(c1, f1);
}

// ISO 8601 ("2026-11-07T03:00:00-05:00") -> UTC epoch seconds, without libc time zones. -1 if not a time.
inline long long iso_epoch(const std::string &v) {
  if (v.size() < 19) return -1;
  int Y, M, D, h, mi, s;
  if (sscanf(v.c_str(), "%d-%d-%dT%d:%d:%d", &Y, &M, &D, &h, &mi, &s) != 6) return -1;
  long off = 0;
  size_t tz = v.find_first_of("+-Z", 19);
  if (tz != std::string::npos && v[tz] != 'Z') {
    int oh = 0, om = 0;
    sscanf(v.c_str() + tz + 1, "%d:%d", &oh, &om);
    off = (oh * 60L + om) * 60L * (v[tz] == '-' ? -1 : 1);
  }
  int y = Y - (M <= 2);
  int era = (y >= 0 ? y : y - 399) / 400;
  unsigned yoe = (unsigned) (y - era * 400);
  unsigned doy = (153 * (M + (M > 2 ? -3 : 9)) + 2) / 5 + D - 1;
  unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  long long days = (long long) era * 146097 + (long long) doe - 719468;
  return days * 86400 + h * 3600 + mi * 60 + s - off;
}

// "2026-11-07T03:00:00-05:00" -> "3:00 AM" (the time is already local in Ember & Tide's timestamps)
inline std::string clock12(const std::string &v) {
  if (v.size() < 16) return "";
  int h = atoi(v.substr(11, 2).c_str()), m = atoi(v.substr(14, 2).c_str());
  const char *ap = h >= 12 ? "PM" : "AM";
  int h12 = h % 12 == 0 ? 12 : h % 12;
  char b[16];
  snprintf(b, sizeof(b), "%d:%02d %s", h12, m, ap);
  return b;
}

inline std::string mmss(long secs) {
  if (secs < 0) secs = 0;
  char b[12];
  snprintf(b, sizeof(b), "%ld:%02ld", secs / 60, secs % 60);
  return b;
}

// "now|Deep|81;3:00|Late night|81;9:45|Side off|" -> row i, field f (0 time, 1 name, 2 temp)
inline std::string tonight_field(const std::string &s, int row, int field) {
  int r = 0;
  size_t start = 0;
  while (r < row) {
    size_t p = s.find(';', start);
    if (p == std::string::npos) return "";
    start = p + 1;
    r++;
  }
  size_t end = s.find(';', start);
  std::string line = s.substr(start, end == std::string::npos ? std::string::npos : end - start);
  size_t a = line.find('|');
  if (a == std::string::npos) return field == 0 ? line : "";
  size_t b = line.find('|', a + 1);
  if (field == 0) return line.substr(0, a);
  if (field == 1) return line.substr(a + 1, b == std::string::npos ? std::string::npos : b - a - 1);
  std::string t = b == std::string::npos ? "" : line.substr(b + 1);
  return t.empty() ? "" : t + "\xC2\xB0";
}

}  // namespace et
