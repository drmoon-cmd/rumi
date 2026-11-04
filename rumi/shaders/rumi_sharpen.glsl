// Rumi 선명하게: 확대가 끝난 화면에 대비 적응형 샤프닝을 한 번 더 한다.
// 이미 대비가 강한 곳(글자, 윤곽선)은 덜, 흐릿한 곳은 더 선명하게 해서 테두리 번짐(헤일로)을 줄인다.

//!DESC Rumi 선명하게
//!HOOK SCALED
//!BIND HOOKED

#define STRENGTH 0.6

vec4 hook() {
    vec4 c = HOOKED_tex(HOOKED_pos);
    vec3 up = HOOKED_texOff(vec2(0.0, -1.0)).rgb;
    vec3 dn = HOOKED_texOff(vec2(0.0, 1.0)).rgb;
    vec3 lf = HOOKED_texOff(vec2(-1.0, 0.0)).rgb;
    vec3 rt = HOOKED_texOff(vec2(1.0, 0.0)).rgb;

    vec3 lo = min(c.rgb, min(min(up, dn), min(lf, rt)));
    vec3 hi = max(c.rgb, max(max(up, dn), max(lf, rt)));
    // 주변과 차이가 클수록(이미 선명할수록) 0 에 가까워진다
    vec3 amount = sqrt(clamp(min(lo, 1.0 - hi) / max(hi, 1e-4), 0.0, 1.0));
    vec3 weight = amount * (-1.0 / mix(8.0, 5.0, STRENGTH));

    vec3 sharp = (c.rgb + (up + dn + lf + rt) * weight) / (1.0 + 4.0 * weight);
    return vec4(clamp(sharp, 0.0, 1.0), c.a);
}
