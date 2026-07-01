# `index.html` 教学文档

## 文件概述

该文件是一个单页静态演示，展示了带有粒子连线背景、鼠标交互（排斥）和点击涟漪特效的酷炫界面。页面核心元素有：背景 Canvas、居中内容卡片（hero）、按钮彩蛋和点击涟漪层。

文件路径： [index.html](index.html)

## 结构分解（自上而下）

- **头部（head）**: 包含字符集、响应式 meta、页面标题以及内联 CSS 样式。
- **样式（style）**: 定义全局重置、背景 Canvas、卡片样式、按钮样式、涟漪样式、关键帧动画以及响应式规则。
- **主体（body）**:
  - `canvas#bg-canvas`：用于绘制背景粒子与连线。
  - `.hero`：居中半透明卡片，包含 `h1` 标题、说明 `p` 和 `button#magic-btn`。
  - `#ripple-container`：在点击时挂载并播放白色边框涟漪元素。
- **脚本（script）**: 用原生 JavaScript 实现三部分功能：粒子背景、全局点击涟漪、按钮彩蛋效果。

## 关键代码与作用

- 粒子系统
  - `Particle` 类：封装单个粒子的坐标、速度、尺寸、透明度与更新/绘制方法。
  - `particles` 数组与 `COUNT = 120`：初始化固定数量的粒子。
  - `animateParticles()`：主渲染循环，清屏 -> 更新与绘制粒子 -> 绘制粒子间连线 -> `requestAnimationFrame` 调度下一帧。
  - `drawLines()`：遍历粒子对，若距离小于 `MAX_DIST` 则绘制连接线，线透明度与距离成反比。
  - 鼠标交互：通过 `document.addEventListener('mousemove', ...)` 捕获鼠标位置，`interactLoop()` 在独立循环中对粒子施加排斥力（近鼠标时向外推）。

- 点击涟漪
  - 全局 `click` 事件创建一个带 `.ripple` 类的 div 放入 `#ripple-container`，CSS 动画 `ripple-anim` 控制从小到大再淡出，1 秒后移除。

- 按钮彩蛋（`#magic-btn`）
  - 点击时阻止事件冒泡（`e.stopPropagation()`），切换按钮线性渐变背景颜色；
  - 在按钮中心生成若干小彩点，利用 `transform` + 过渡动画向外飞散并淡出，最后移除 DOM 元素。

## 可配置项（常见自定义点）

- 粒子数量：修改 `COUNT` 可以增减粒子数量（性能开销线性增长）。
- 连接距离：修改 `MAX_DIST` 控制连线密度与视觉密集度。
- 粒子颜色与透明度：在 `Particle.draw()` 中调整 `rgba(180,180,255,...)`。
- 背景与卡片颜色：在 CSS 中修改 `body` 背景、`.hero` 背景与边框样式。
- 按钮色板：修改脚本中 `hue` 数组以变更彩蛋颜色序列。

## 性能与兼容性建议

- 在移动设备上将 `COUNT` 降低到 40-60，或在小屏幕上停止绘制连线以节省 CPU。建议在 `requestAnimationFrame` 循环里根据 `devicePixelRatio` 和窗口大小调整绘制分辨率。
- 使用 `will-change`、合适的 `translateZ(0)` 或 `pointer-events` 控制，避免不必要的重排和回流。
- Canvas 绘制大量对象时避免频繁分配（比如不要在循环内 new 大量对象）。

## 无障碍（Accessibility）与可用性提示

- 按钮应有 `aria-label` 或可见文本（当前有 emoji，考虑补充 `aria-label`）。
- 对于依赖动画的用户，提供 CSS 或 JS 开关以支持减少动画（遵循 prefers-reduced-motion 媒体查询）。

## 调试要点（常见问题定位）

- Canvas 未显示：检查 `canvas` 宽高是否为 0（`resize()` 是否被调用），以及 `getContext('2d')` 返回值。
- 粒子不动或报错：在控制台查看 JS 错误，确保变量如 `W/H` 在 `Particle.reset()` 中存在且未被 undefined 覆盖。
- 点击涟漪看不到：确认 `#ripple-container` 的 `z-index` 高于背景且 `pointer-events: none`（以免阻挡交互）。

## 快速修改示例

- 将粒子数量减半（性能优化）：

```js
// 将 COUNT 从 120 改为 60
const COUNT = 60;
```

- 在小屏幕禁用连线：

```css
@media (max-width: 600px) {
  /* 在 JS 中可以检测并跳过 drawLines()，这里仅示意 CSS 方案 */
}
```

## 结论与下一步建议

这是一个结构清晰、可扩展的演示页面，适合用作视觉引导或着陆页的交互背景。下一步可考虑：

- 将脚本拆分到独立文件（例如 `js/bg.js`、`js/ui.js`），便于维护和单元测试；
- 添加 `prefers-reduced-motion` 支持和配置面板以让用户调节粒子/连线密度；
- 将颜色与常量抽离为顶部配置对象，便于主题化。

---

如果需要，我可以：
- 将文档转为中文/英文两版；
- 分离并注释脚本成多个文件并提交改动；
- 在页面中添加“关闭动画”开关并实现存储用户偏好。
