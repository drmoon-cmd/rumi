"""VR 펼치기를 그래픽 카드에서: mpv 가 360°/180° 원본을 화면 밖 텍스처에 그리면, 작은 셰이더가
보는 방향의 평면 화면으로 펼쳐 창에 그린다.

방향·시야는 셰이더 값(uniform)이라 바꿔도 비용이 거의 없다. 예전 방식(ffmpeg v360 필터)은
CPU 에서 돌았고, 방향을 바꿀 때마다 큰 계산표를 새로 만들어 끌 때 화면이 굳고 PC 전체가 느려졌다.
"""

from __future__ import annotations

import struct

from PySide6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLShader,
    QOpenGLShaderProgram, QOpenGLVertexArrayObject,
)

GL_FRAMEBUFFER = 0x8D40
GL_TEXTURE_2D = 0x0DE1
GL_TEXTURE0 = 0x84C0
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_REPEAT = 0x2901
GL_CLAMP_TO_EDGE = 0x812F
GL_LINEAR = 0x2601
GL_TRIANGLE_STRIP = 0x0005
GL_FLOAT = 0x1406
GL_BLEND = 0x0BE2
GL_SCISSOR_TEST = 0x0C11
GL_DEPTH_TEST = 0x0B71
GL_MAX_TEXTURE_SIZE = 0x0D33
GL_COLOR_BUFFER_BIT = 0x4000
GL_RGBA8 = 0x8058

MAX_SOURCE = 8192  # 원본 텍스처 크기 상한 (8K VR 까지)

VERTEX = """
attribute vec2 vert;
varying vec2 pos;
void main() {
    pos = vert;
    gl_Position = vec4(vert, 0.0, 1.0);
}
"""

FRAGMENT = """
#ifdef GL_ES
precision highp float;
#endif
uniform sampler2D tex;
uniform float yaw;      // 라디안
uniform float pitch;
uniform float tan_half; // tan(가로 시야 / 2)
uniform float aspect;   // 높이 / 너비
uniform float span;     // 가로로 담긴 각도 (라디안): 360° 또는 180°
uniform int stereo;     // 0 한 화면, 1 위아래(위쪽 눈), 2 좌우(왼쪽 눈)
varying vec2 pos;

void main() {
    vec3 d = normalize(vec3(pos.x * tan_half, pos.y * tan_half * aspect, 1.0));
    float cp = cos(pitch), sp = sin(pitch);
    d = vec3(d.x, d.y * cp + d.z * sp, -d.y * sp + d.z * cp);
    float cy = cos(yaw), sy = sin(yaw);
    d = vec3(d.x * cy + d.z * sy, d.y, -d.x * sy + d.z * cy);
    float lon = atan(d.x, d.z);
    float lat = asin(clamp(d.y, -1.0, 1.0));
    if (abs(lon) > span * 0.5) {
        gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    // mpv 가 flip_y 없이 그린 텍스처는 그림의 위쪽이 v = 0 쪽에 있다
    vec2 uv = vec2(lon / span + 0.5, 0.5 - lat / 3.14159265358979);
    if (stereo == 1)
        uv.y *= 0.5;               // 위쪽 절반 = 왼쪽 눈
    else if (stereo == 2)
        uv.x *= 0.5;
    gl_FragColor = texture2D(tex, uv);
}
"""


class VrRenderer:
    """GL 컨텍스트가 활성일 때만 호출한다 (MpvWidget.paintGL 안)."""

    def __init__(self, functions):
        self.f = functions
        self.fbo: QOpenGLFramebufferObject | None = None
        self.program = QOpenGLShaderProgram()
        ok = (self.program.addShaderFromSourceCode(QOpenGLShader.Vertex, VERTEX)
              and self.program.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAGMENT)
              and self.program.link())
        self.error = None if ok else (self.program.log() or "셰이더를 만들지 못했습니다")
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()  # 지원하지 않는 환경에서는 만들어지지 않는다 (그래도 동작하도록 그릴 때마다 속성을 지정)
        self.vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self.vbo.create()
        self.vbo.bind()
        quad = struct.pack("8f", -1, -1, 1, -1, -1, 1, 1, 1)
        self.vbo.allocate(quad, len(quad))
        self.vbo.release()
        try:
            self.max_tex = int(self.f.glGetIntegerv(GL_MAX_TEXTURE_SIZE)) if hasattr(self.f, "glGetIntegerv") else 8192
        except Exception:
            self.max_tex = 8192

    def source_size(self, video_w: int, video_h: int) -> tuple[int, int]:
        w, h = max(video_w, 16), max(video_h, 16)
        cap = min(MAX_SOURCE, self.max_tex or MAX_SOURCE)
        scale = min(1.0, cap / w, cap / h)
        return int(w * scale), int(h * scale)

    def render(self, render_ctx, target_fbo: int, out_w: int, out_h: int, video_size: tuple[int, int],
               view: dict) -> None:
        w, h = self.source_size(*video_size)
        if self.fbo is None or (self.fbo.width(), self.fbo.height()) != (w, h):
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setInternalTextureFormat(GL_RGBA8)
            self.fbo = QOpenGLFramebufferObject(w, h, fmt)
        # 1) mpv 가 원본 전체(360° 그림)를 화면 밖 텍스처에 그린다
        render_ctx.render(flip_y=False, opengl_fbo={"w": w, "h": h, "fbo": self.fbo.handle()})
        # 2) 보는 방향으로 펼쳐 창에 그린다
        f = self.f
        f.glBindFramebuffer(GL_FRAMEBUFFER, target_fbo)
        f.glViewport(0, 0, out_w, out_h)
        f.glDisable(GL_BLEND)
        f.glDisable(GL_SCISSOR_TEST)
        f.glDisable(GL_DEPTH_TEST)
        f.glClearColor(0.0, 0.0, 0.0, 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT)
        if self.error:
            return
        f.glActiveTexture(GL_TEXTURE0)
        f.glBindTexture(GL_TEXTURE_2D, self.fbo.texture())
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)       # 360° 이음매가 끊기지 않게
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        f.glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        p = self.program
        p.bind()
        p.setUniformValue1i(p.uniformLocation("tex"), 0)
        p.setUniformValue1f(p.uniformLocation("yaw"), view["yaw"])
        p.setUniformValue1f(p.uniformLocation("pitch"), view["pitch"])
        p.setUniformValue1f(p.uniformLocation("tan_half"), view["tan_half"])
        p.setUniformValue1f(p.uniformLocation("aspect"), out_h / max(out_w, 1))
        p.setUniformValue1f(p.uniformLocation("span"), view["span"])
        p.setUniformValue1i(p.uniformLocation("stereo"), view["stereo"])
        if self.vao.isCreated():
            self.vao.bind()
        self.vbo.bind()
        loc = p.attributeLocation("vert")
        p.enableAttributeArray(loc)
        p.setAttributeBuffer(loc, GL_FLOAT, 0, 2)
        f.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        p.disableAttributeArray(loc)
        self.vbo.release()
        if self.vao.isCreated():
            self.vao.release()
        p.release()
        f.glBindTexture(GL_TEXTURE_2D, 0)

    def free(self) -> None:
        self.fbo = None
        self.vbo.destroy()
        if self.vao.isCreated():
            self.vao.destroy()
        self.program.removeAllShaders()
