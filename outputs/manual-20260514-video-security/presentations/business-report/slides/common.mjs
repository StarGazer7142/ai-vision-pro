const C = {
  bg: "#07111F",
  bg2: "#0B1C2E",
  panel: "#0F263A",
  panel2: "#132F46",
  line: "#24506A",
  cyan: "#2EE6FF",
  cyan2: "#00A8D6",
  green: "#3DFFAA",
  amber: "#FFCB66",
  red: "#FF6B6B",
  ink: "#F3FAFF",
  text: "#C8D8E8",
  muted: "#7F9AB2",
  white: "#FFFFFF",
};

const slides = [
  {
    kicker: "PROJECT OVERVIEW",
    title: "AI 视频识别信号平台",
    subtitle: "面向园区、仓库、码头的实时检测、告警闭环与回放追溯系统",
    claim: "从检测 Demo 升级为可运行、可管理、可追溯的智能安防业务系统。",
    visual: "cover",
  },
  {
    kicker: "BACKGROUND",
    title: "为什么需要 AI 视频识别平台",
    claim: "传统监控只负责记录，真正的风险在于发现慢、处置慢、追溯难。",
    bullets: ["监控画面多，人工注意力有限", "异常事件发现不及时", "告警、回放、处理记录割裂", "缺少可审计、可复盘的数据闭环"],
    visual: "pain",
  },
  {
    kicker: "PROJECT GOAL",
    title: "从检测 Demo 到完整业务闭环",
    claim: "平台把视频接入、AI 检测、规则判断、告警处置、回放分析和审计运维串成闭环。",
    metrics: [
      ["实时发现", "自动识别人员和异常行为"],
      ["快速处置", "告警进入大屏并支持指派处理"],
      ["证据追溯", "关联回放、截图、视频片段和分析结果"],
      ["稳定交付", "具备权限、备份、部署脚本和文档"],
    ],
    visual: "four",
  },
  {
    kicker: "ARCHITECTURE",
    title: "系统总体架构",
    claim: "前端、接口、识别、规则、存储和运维六层分工明确，支撑后续扩展。",
    layers: ["前端展示层", "后端接口层", "视觉识别层", "规则引擎层", "数据存储层", "运维部署层"],
    details: ["监控矩阵 / 告警大屏 / 设备管理", "FastAPI 认证、设备、告警、回放、日志", "本地 YOLO 人员检测", "围栏翻越、区域滞留、方向和确认帧", "SQLite 用户、会话、告警、设置和审计", "日志轮转、备份清理、Docker、Nginx"],
    visual: "architecture",
  },
  {
    kicker: "SCENARIOS",
    title: "覆盖三类高频安防场景",
    claim: "园区、仓库、码头的共同需求是实时识别、规则判断和证据闭环。",
    metrics: [
      ["园区围栏", "人员翻越、围栏区域异常滞留"],
      ["仓库区域", "长时间停留、异常聚集"],
      ["码头作业区", "作业区域异常停留"],
      ["手机接入", "同一局域网手机可作为实时摄像头"],
    ],
    visual: "scenario",
  },
  {
    kicker: "DEVICE ACCESS",
    title: "正式设备管理替代 Debug 接入",
    claim: "摄像头新增、编辑、删除、分组、启停、流地址配置和在线检测都进入正式页面。",
    flow: ["管理员登录", "进入设备接入管理", "新增摄像头与流地址", "选择场景并启用", "保存后热重载", "监控矩阵查看"],
    visual: "device",
  },
  {
    kicker: "AI + RULES",
    title: "AI 检测 + 规则引擎",
    claim: "YOLO 负责发现人员目标，规则引擎负责判断是否构成业务事件。",
    metrics: [
      ["检测框", "实时叠加人员目标"],
      ["围栏线", "识别穿越边界"],
      ["滞留区", "按区域和时间阈值判断"],
      ["确认帧", "降低瞬时误报"],
    ],
    visual: "rules",
  },
  {
    kicker: "ALERT LOOP",
    title: "告警不是展示，而是可处理的业务事件",
    claim: "告警支持筛选、搜索、分页、导出，并能确认、指派、完成或标记误报。",
    flow: ["新告警", "已确认", "处理中", "已完成", "误报归档"],
    visual: "alert",
  },
  {
    kicker: "REPLAY",
    title: "告警自动关联证据链",
    claim: "回放中心把摄像头、时间、场景、规则和录像文件稳定关联，减少人工找录像时间。",
    bullets: ["自动定位录像文件", "支持播放、下载、短片段生成", "显示事件偏移点和检测框叠加", "视频理解生成文字说明，辅助复盘"],
    visual: "evidence",
  },
  {
    kicker: "SECURITY",
    title: "从演示入口升级为正式管理体系",
    claim: "角色权限、Token 会话、Debug 默认关闭和操作审计让系统更接近生产要求。",
    metrics: [
      ["角色", "超级管理员 / 管理员 / 值班 / 只读"],
      ["认证", "密码哈希、登录失败锁定、退出失效"],
      ["权限", "API 统一校验、回放下载路径限制"],
      ["审计", "规则、设备、用户、告警、备份清理留痕"],
    ],
    visual: "security",
  },
  {
    kicker: "DELIVERY",
    title: "具备可运行、可守护、可交付能力",
    claim: "项目补齐日志轮转、断流重连、健康检查、备份清理和标准部署文件。",
    bullets: ["Dockerfile / docker-compose.yml", "deploy/nginx.conf", "scripts/run_supervisor.ps1", "scripts/ops_maintenance.py", ".env.example"],
    visual: "delivery",
  },
  {
    kicker: "TEST RESULT",
    title: "功能、性能、准确率测试口径",
    claim: "后端单元测试、黑盒接口测试和产品完整性测试均通过；真实模型准确率需继续基于标注集统计。",
    metrics: [
      ["44", "后端单元测试通过"],
      ["16", "黑盒接口测试通过"],
      ["7", "产品完整性测试通过"],
      ["31.15 ms", "历史平均 HTTP 响应"],
    ],
    visual: "test",
  },
  {
    kicker: "VALUE",
    title: "项目价值总结",
    claim: "平台价值不只在识别，而在把风险发现、处置记录和证据追溯连起来。",
    metrics: [
      ["提升安防效率", "主动识别异常并告警"],
      ["提升处置闭环", "备注、指派、误报、完成状态可追踪"],
      ["降低试点成本", "普通电脑、摄像头和手机均可接入"],
      ["具备交付基础", "权限、安全、运维、部署和文档齐备"],
    ],
    visual: "value",
  },
  {
    kicker: "ROADMAP",
    title: "下一阶段优化方向",
    claim: "后续重点是长时间稳定性、真实准确率、多路并发、通知和生产级部署。",
    flow: ["24/72 小时稳定性", "真实标注集与PR统计", "WebRTC / HLS 多路优化", "短信 / 邮件 / 企业微信 / 钉钉", "HTTPS、域名和服务器部署", "误报反馈优化模型阈值"],
    visual: "roadmap",
  },
  {
    kicker: "CONCLUSION",
    title: "项目结论",
    claim: "当前平台已从单一检测 Demo 升级为具备实时监控、设备管理、告警闭环、回放追溯、权限审计和运维交付能力的完整业务系统。",
    subtitle: "让摄像头从被动录像设备，升级为主动发现风险、辅助处置决策的智能安防节点。",
    visual: "closing",
  },
];

function text(slide, ctx, value, x, y, w, h, opts = {}) {
  const shape = ctx.addText(slide, {
    text: String(value ?? ""),
    left: x,
    top: y,
    width: w,
    height: h,
    fontSize: opts.size ?? 18,
    color: opts.color ?? C.text,
    bold: Boolean(opts.bold),
    typeface: opts.face ?? "Microsoft YaHei",
    align: opts.align ?? "left",
    valign: opts.valign ?? "top",
    fill: opts.fill ?? "#00000000",
    line: opts.line ?? ctx.line(),
    insets: opts.insets ?? { left: 0, right: 0, top: 0, bottom: 0 },
  });
  return shape;
}

function rect(slide, ctx, x, y, w, h, fill, opts = {}) {
  return ctx.addShape(slide, {
    left: x,
    top: y,
    width: w,
    height: h,
    geometry: opts.geometry ?? "rect",
    fill,
    line: opts.line ?? ctx.line(opts.lineColor ?? "#00000000", opts.lineWidth ?? 0),
  });
}

function line(slide, ctx, x, y, w, color = C.line, weight = 1) {
  rect(slide, ctx, x, y, w, weight, color);
}

function wrapCn(value, max = 24, maxLines = 3) {
  const raw = String(value || "").replace(/\s+/g, "");
  const lines = [];
  for (let i = 0; i < raw.length && lines.length < maxLines; i += max) {
    let part = raw.slice(i, i + max);
    if (i + max < raw.length && lines.length === maxLines - 1) part = `${part.slice(0, Math.max(1, max - 1))}...`;
    lines.push(part);
  }
  return lines.join("\n");
}

function background(slide, ctx) {
  rect(slide, ctx, 0, 0, ctx.W, ctx.H, C.bg);
  rect(slide, ctx, 0, 0, ctx.W, 720, "#00000000", { lineColor: "#00000000" });
  for (let x = 80; x < 1280; x += 80) line(slide, ctx, x, 0, 1, "#10243A", 1);
  for (let y = 80; y < 720; y += 80) line(slide, ctx, 0, y, 1280, "#10243A", 1);
  rect(slide, ctx, 0, 0, 1280, 42, "#081827");
  line(slide, ctx, 0, 42, 1280, "#143D58", 1);
}

function header(slide, ctx, data, page) {
  text(slide, ctx, data.kicker, 48, 17, 360, 14, { size: 9, color: C.cyan, bold: true });
  text(slide, ctx, `AI 视频识别信号平台 / ${String(page).padStart(2, "0")}`, 1010, 16, 220, 14, { size: 8, color: C.muted, align: "right" });
  text(slide, ctx, data.title, 48, 74, 770, 62, { size: page === 1 ? 42 : 31, color: C.ink, bold: true });
  text(slide, ctx, wrapCn(data.claim, 32, 2), 50, page === 1 ? 226 : 139, page === 1 ? 640 : 790, page === 1 ? 74 : 50, { size: page === 1 ? 22 : 18, color: C.text, bold: page === 1 });
}

function footer(slide, ctx, page) {
  line(slide, ctx, 48, 674, 1184, "#143D58", 1);
  text(slide, ctx, "课程项目答辩 / 商业汇报展示版", 48, 686, 280, 12, { size: 7.5, color: C.muted });
  text(slide, ctx, "2026.05", 1140, 686, 90, 12, { size: 7.5, color: C.muted, align: "right" });
}

function card(slide, ctx, x, y, w, h, title, body, accent = C.cyan) {
  rect(slide, ctx, x, y, w, h, C.panel, { lineColor: "#1D536F", lineWidth: 1 });
  rect(slide, ctx, x, y, 4, h, accent);
  text(slide, ctx, title, x + 18, y + 16, w - 36, 24, { size: 16, color: C.ink, bold: true });
  text(slide, ctx, wrapCn(body, 18, 3), x + 18, y + 46, w - 36, h - 56, { size: 12.5, color: C.text });
}

function bulletList(slide, ctx, bullets, x, y, w) {
  bullets.forEach((item, i) => {
    const yy = y + i * 62;
    rect(slide, ctx, x, yy + 6, 30, 30, i % 2 ? C.green : C.cyan, { lineColor: "#00000000" });
    text(slide, ctx, String(i + 1).padStart(2, "0"), x + 5, yy + 12, 20, 16, { size: 9, color: C.bg, bold: true, align: "center" });
    text(slide, ctx, wrapCn(item, 28, 2), x + 46, yy, w - 46, 42, { size: 17, color: C.ink, bold: true });
  });
}

function metricGrid(slide, ctx, items, x = 60, y = 230, cols = 2) {
  const w = cols === 4 ? 252 : cols === 3 ? 360 : 535;
  const gap = cols === 4 ? 25 : 38;
  const h = 128;
  items.forEach((item, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    const xx = x + col * (w + gap);
    const yy = y + row * (h + 34);
    card(slide, ctx, xx, yy, w, h, item[0], item[1], i % 2 ? C.green : C.cyan);
  });
}

function compactGrid(slide, ctx, items, x, y) {
  items.forEach((item, i) => {
    const xx = x + (i % 2) * 255;
    const yy = y + Math.floor(i / 2) * 136;
    card(slide, ctx, xx, yy, 230, 112, item[0], item[1], i % 2 ? C.green : C.cyan);
  });
}

function drawMonitor(slide, ctx, x, y, w, h) {
  rect(slide, ctx, x, y, w, h, "#091625", { lineColor: "#2B7892", lineWidth: 2 });
  for (let i = 0; i < 4; i++) {
    const xx = x + 26 + (i % 2) * ((w - 74) / 2 + 22);
    const yy = y + 26 + Math.floor(i / 2) * ((h - 82) / 2 + 22);
    rect(slide, ctx, xx, yy, (w - 74) / 2, (h - 82) / 2, i === 1 ? "#102D3E" : "#0D2334", { lineColor: "#1F5E78", lineWidth: 1 });
    rect(slide, ctx, xx + 24, yy + 24, 86, 42, "#112A3F", { lineColor: C.cyan2, lineWidth: 2 });
    text(slide, ctx, i === 1 ? "ALERT" : "CAM", xx + 20, yy + 82, 95, 18, { size: 12, color: i === 1 ? C.amber : C.cyan, bold: true, align: "center" });
    line(slide, ctx, xx + 138, yy + 42, 86, i === 1 ? C.amber : C.green, 3);
    line(slide, ctx, xx + 138, yy + 64, 116, "#1F5E78", 3);
  }
}

function drawFlow(slide, ctx, data, colors = [C.cyan, C.green, C.amber]) {
  const stepW = data.flow.length > 5 ? 160 : 190;
  const gap = data.flow.length > 5 ? 28 : 38;
  const start = 80;
  data.flow.forEach((item, i) => {
    const x = start + i * (stepW + gap);
    rect(slide, ctx, x, 310, stepW, 96, C.panel, { lineColor: colors[i % colors.length], lineWidth: 1 });
    text(slide, ctx, String(i + 1).padStart(2, "0"), x + 16, 326, 40, 22, { size: 18, color: colors[i % colors.length], bold: true });
    text(slide, ctx, wrapCn(item, 8, 2), x + 16, 360, stepW - 32, 34, { size: 15, color: C.ink, bold: true, align: "center" });
    if (i < data.flow.length - 1) {
      line(slide, ctx, x + stepW + 6, 358, gap - 12, "#2B7892", 2);
      rect(slide, ctx, x + stepW + gap - 12, 353, 10, 10, "#2B7892");
    }
  });
}

function drawArchitecture(slide, ctx, data) {
  data.layers.forEach((layer, i) => {
    const y = 210 + i * 68;
    const accent = [C.cyan, C.green, C.amber, C.cyan2, "#7BD5FF", "#B2FFE1"][i];
    rect(slide, ctx, 80, y, 280, 44, C.panel, { lineColor: accent, lineWidth: 1 });
    text(slide, ctx, layer, 104, y + 12, 230, 18, { size: 16, color: C.ink, bold: true });
    line(slide, ctx, 360, y + 22, 80, accent, 2);
    rect(slide, ctx, 440, y - 4, 700, 52, "#0B2032", { lineColor: "#1D536F", lineWidth: 1 });
    text(slide, ctx, data.details[i], 466, y + 12, 650, 18, { size: 14, color: C.text });
  });
}

function drawBars(slide, ctx) {
  const bars = [
    ["平均响应", 31.15, "31.15 ms", C.cyan],
    ["最大响应", 104, "104 ms", C.amber],
    ["规则测试", 100, "通过", C.green],
    ["黑盒接口", 100, "通过", C.green],
  ];
  bars.forEach((b, i) => {
    const y = 308 + i * 56;
    text(slide, ctx, b[0], 110, y, 120, 18, { size: 14, color: C.ink, bold: true });
    rect(slide, ctx, 250, y + 3, 560, 14, "#153149");
    rect(slide, ctx, 250, y + 3, Math.min(560, b[1] / 104 * 560), 14, b[3]);
    text(slide, ctx, b[2], 835, y - 1, 120, 20, { size: 15, color: b[3], bold: true });
  });
}

function drawVisual(slide, ctx, data, page) {
  switch (data.visual) {
    case "cover":
      drawMonitor(slide, ctx, 710, 126, 470, 318);
      rect(slide, ctx, 750, 492, 380, 54, C.panel, { lineColor: C.cyan, lineWidth: 1 });
      text(slide, ctx, "实时检测 · 告警闭环 · 回放追溯 · 运维交付", 775, 510, 330, 18, { size: 15, color: C.cyan, bold: true, align: "center" });
      text(slide, ctx, "汇报人：项目组    日期：2026年5月", 52, 570, 560, 22, { size: 16, color: C.muted });
      break;
    case "pain":
      bulletList(slide, ctx, data.bullets, 96, 238, 620);
      drawMonitor(slide, ctx, 760, 230, 350, 240);
      text(slide, ctx, "人工值守瓶颈", 830, 505, 210, 24, { size: 20, color: C.amber, bold: true, align: "center" });
      break;
    case "architecture":
      drawArchitecture(slide, ctx, data);
      break;
    case "device":
    case "alert":
    case "roadmap":
      drawFlow(slide, ctx, data, data.visual === "alert" ? [C.red, C.amber, C.cyan, C.green, C.muted] : [C.cyan, C.green, C.amber]);
      break;
    case "rules":
      rect(slide, ctx, 112, 230, 472, 286, "#091625", { lineColor: "#2B7892", lineWidth: 2 });
      rect(slide, ctx, 194, 300, 115, 64, "#173047", { lineColor: C.cyan, lineWidth: 2 });
      line(slide, ctx, 143, 430, 382, C.amber, 4);
      rect(slide, ctx, 356, 284, 150, 150, "#12304A", { lineColor: C.green, lineWidth: 2 });
      text(slide, ctx, "YOLO 目标检测", 205, 377, 105, 18, { size: 13, color: C.cyan, align: "center" });
      text(slide, ctx, "规则区域", 383, 344, 96, 18, { size: 13, color: C.green, align: "center" });
      compactGrid(slide, ctx, data.metrics, 660, 228);
      break;
    case "evidence":
      bulletList(slide, ctx, data.bullets, 88, 236, 580);
      rect(slide, ctx, 760, 240, 360, 210, "#091625", { lineColor: C.cyan, lineWidth: 2 });
      line(slide, ctx, 792, 404, 288, C.green, 3);
      rect(slide, ctx, 866, 302, 142, 56, "#173047", { lineColor: C.amber, lineWidth: 2 });
      text(slide, ctx, "证据链", 895, 320, 84, 20, { size: 18, color: C.amber, bold: true, align: "center" });
      text(slide, ctx, "告警 → 回放 → 片段 → 说明 → 复盘", 765, 486, 350, 20, { size: 15, color: C.text, align: "center" });
      break;
    case "delivery":
      bulletList(slide, ctx, data.bullets, 110, 238, 510);
      ["健康检查", "日志轮转", "断流重连", "备份清理", "标准部署"].forEach((item, i) => {
        const x = 710 + (i % 2) * 210;
        const y = 245 + Math.floor(i / 2) * 105;
        card(slide, ctx, x, y, 185, 74, item, "可运行、可守护、可交付", i % 2 ? C.green : C.cyan);
      });
      break;
    case "test":
      metricGrid(slide, ctx, data.metrics, 78, 210, 4);
      drawBars(slide, ctx);
      text(slide, ctx, "说明：当前准确率以规则引擎测试通过为主，真实视觉模型 Precision / Recall 需后续基于人工标注集统计。", 110, 562, 960, 26, { size: 15, color: C.muted });
      break;
    case "closing":
      rect(slide, ctx, 120, 252, 1040, 182, C.panel, { lineColor: C.cyan, lineWidth: 2 });
      text(slide, ctx, wrapCn(data.claim, 36, 3), 165, 292, 950, 84, { size: 24, color: C.ink, bold: true, align: "center" });
      text(slide, ctx, data.subtitle, 205, 468, 870, 28, { size: 19, color: C.cyan, bold: true, align: "center" });
      break;
    default:
      metricGrid(slide, ctx, data.metrics, 78, 230, 2);
  }
}

export async function buildSlide(presentation, ctx, index) {
  const slide = presentation.slides.add();
  const data = slides[index - 1];
  background(slide, ctx);
  header(slide, ctx, data, index);
  drawVisual(slide, ctx, data, index);
  footer(slide, ctx, index);
  return slide;
}
