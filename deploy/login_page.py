"""登录页渲染：左品牌叙事 + 右登录卡的分屏版式。

单独成文件的原因：这段 HTML/CSS 有大量花括号，塞进 main.py 的 f-string 里
要么全转义、要么用 % 拼接，改一次错一次。这里用 __TOKEN__ 占位 + replace，
结构一眼能看清。

设计对齐产品内的阅片工作台令牌（styles.css 的 v2 设计系统）：
背景三层、发丝线边框、highlight 只标「当前激活」、字阶 11/12/13/14/20。
"""

from __future__ import annotations

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex,nofollow" />
<title>登录 · 医学影像阅片与手术规划系统</title>
<style>
  :root {
    --bg: hsl(210 24% 7%);
    --bg-2: hsl(210 22% 9%);
    --panel: hsl(210 17% 11.5%);
    --card: hsl(210 17% 13%);
    --border: hsl(210 12% 18%);
    --border-strong: hsl(210 12% 26%);
    --fg: hsl(210 16% 93%);
    --fg-dim: hsl(210 12% 72%);
    --muted: hsl(210 10% 55%);
    --primary: hsl(199 89% 56%);
    --primary-2: hsl(206 92% 50%);
    --input: hsl(210 16% 16%);
    --danger: hsl(0 72% 60%);
    --font: 'Inter', -apple-system, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB',
      'Microsoft YaHei', sans-serif;
    --mono: 'SFMono-Regular', 'IBM Plex Mono', Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    display: grid;
    place-items: center;
    padding: 28px 20px;
    background:
      radial-gradient(70% 55% at 20% 0%, hsl(203 60% 16% / .55) 0%, hsl(210 24% 7% / 0) 60%),
      radial-gradient(60% 50% at 92% 100%, hsl(212 70% 18% / .45) 0%, hsl(210 24% 7% / 0) 55%),
      var(--bg);
    color: var(--fg);
    font-family: var(--font);
    font-size: 13px;
    -webkit-font-smoothing: antialiased;
  }

  /* ---------- 外壳 ---------- */
  .shell {
    width: 100%;
    max-width: 1010px;
    display: grid;
    grid-template-columns: 1.06fr .94fr;
    border: 1px solid var(--border);
    border-radius: 18px;
    overflow: hidden;
    background: var(--panel);
    box-shadow:
      0 1px 0 hsl(210 40% 30% / .12) inset,
      0 40px 90px -50px hsl(210 60% 4% / .95);
  }

  /* ---------- 左侧品牌面板 ---------- */
  .brand {
    position: relative;
    padding: 34px 34px 26px;
    display: flex;
    flex-direction: column;
    gap: 22px;
    background:
      repeating-linear-gradient(0deg, hsl(210 30% 40% / .035) 0 1px, transparent 1px 26px),
      repeating-linear-gradient(90deg, hsl(210 30% 40% / .035) 0 1px, transparent 1px 26px),
      linear-gradient(160deg, hsl(205 32% 13%) 0%, hsl(210 26% 9%) 62%);
    border-right: 1px solid var(--border);
    overflow: hidden;
  }
  .brand::after {
    content: '';
    position: absolute;
    width: 340px; height: 340px;
    left: -110px; top: -120px;
    background: radial-gradient(circle, hsl(197 90% 55% / .18) 0%, transparent 68%);
    pointer-events: none;
  }
  .brand > * { position: relative; z-index: 1; }

  .brand-head { display: flex; align-items: center; gap: 11px; }
  .mark {
    width: 34px; height: 34px; border-radius: 9px;
    display: grid; place-items: center;
    background: linear-gradient(150deg, hsl(193 92% 58%), hsl(212 92% 48%));
    color: hsl(210 40% 8%);
    box-shadow: 0 6px 16px -8px hsl(199 89% 56% / .9);
  }
  .brand-head b { display: block; font-size: 13.5px; font-weight: 600; letter-spacing: .2px; }
  .brand-head span {
    display: block; margin-top: 3px;
    font-family: var(--mono); font-size: 10.5px;
    color: var(--muted); letter-spacing: .06em; text-transform: uppercase;
  }

  /* 视口示意 */
  .stage { margin-top: 2px; }
  .stage svg { display: block; width: 100%; height: auto; }

  /* 三视口动态：定位线联动漂移 / 重建扫描线 / 层位标尺 / 焦点脉冲 */
  .stage .mv-x { animation: mvX 11.5s cubic-bezier(.45,0,.55,1) infinite; }
  .stage .mv-y { animation: mvY 8.6s cubic-bezier(.45,0,.55,1) infinite; }
  .stage .mv-z { animation: mvZ 13.2s cubic-bezier(.45,0,.55,1) infinite; }
  .stage .scan { animation: scanSweep 7.2s linear infinite; }
  .stage .scan-b { animation-delay: 1.6s; }
  .stage .scan-c { animation-delay: 3.4s; }
  .stage .tissue { animation: tissueBreath 9.4s ease-in-out infinite; }
  .stage .tissue-b { animation-delay: -3.1s; }
  .stage .tissue-c { animation-delay: -6.2s; }
  .stage .ping {
    transform-box: fill-box; transform-origin: center;
    animation: focusPing 3.4s ease-out infinite;
  }
  .stage .live-dot { animation: dotLive 2.6s ease-in-out infinite; }

  @keyframes mvX {
    0%, 100% { transform: translateX(0); }
    17%      { transform: translateX(9px); }
    39%      { transform: translateX(-6px); }
    61%      { transform: translateX(12px); }
    83%      { transform: translateX(-3px); }
  }
  @keyframes mvY {
    0%, 100% { transform: translateY(0); }
    21%      { transform: translateY(-11px); }
    47%      { transform: translateY(8px); }
    71%      { transform: translateY(-5px); }
  }
  @keyframes mvZ {
    0%, 100% { transform: translateX(0); }
    28%      { transform: translateX(-9px); }
    56%      { transform: translateX(6px); }
    79%      { transform: translateX(-2px); }
  }
  @keyframes scanSweep {
    0%   { transform: translateY(0);     opacity: 0; }
    9%   { opacity: 1; }
    90%  { opacity: 1; }
    100% { transform: translateY(132px); opacity: 0; }
  }
  @keyframes tissueBreath {
    0%, 100% { opacity: .84; }
    50%      { opacity: 1; }
  }
  @keyframes focusPing {
    0%   { transform: scale(1);   opacity: .55; }
    72%  { transform: scale(4.6); opacity: 0; }
    100% { transform: scale(4.6); opacity: 0; }
  }
  @keyframes dotLive {
    0%, 100% { opacity: 1; }
    50%      { opacity: .18; }
  }

  @media (prefers-reduced-motion: reduce) {
    .stage svg * { animation: none !important; }
  }
  .stage-cap {
    display: flex; justify-content: space-between; align-items: center;
    margin-top: 9px; font-size: 11px; color: var(--muted);
  }
  .stage-cap code { font-family: var(--mono); font-size: 10.5px; }

  /* 卖点列表 */
  .points { list-style: none; margin: 0; padding: 0; display: grid; gap: 13px; }
  .points li { display: flex; gap: 11px; align-items: flex-start; }
  .points .ico {
    flex: none; width: 24px; height: 24px; border-radius: 7px;
    display: grid; place-items: center;
    background: hsl(199 89% 56% / .1);
    border: 1px solid hsl(199 89% 56% / .22);
    color: var(--primary);
  }
  .points b { display: block; font-size: 12.5px; font-weight: 600; margin-bottom: 2px; }
  .points p { margin: 0; font-size: 11.5px; line-height: 1.55; color: var(--muted); }

  .brand-foot {
    margin-top: auto; padding-top: 16px;
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
    font-size: 10.5px; color: hsl(210 10% 45%);
  }
  .tag {
    padding: 2.5px 7px; border-radius: 5px;
    border: 1px solid hsl(210 12% 22%);
    background: hsl(210 18% 15%);
    font-family: var(--mono); font-size: 9.5px; letter-spacing: .04em;
    color: hsl(210 12% 62%);
  }

  /* ---------- 右侧登录 ---------- */
  .auth {
    padding: 40px 40px 32px;
    display: flex; flex-direction: column; justify-content: center;
    background: linear-gradient(180deg, hsl(210 18% 12.5%) 0%, hsl(210 18% 11%) 100%);
  }
  .chip {
    align-self: flex-start;
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 9px 4px 8px; border-radius: 999px;
    background: hsl(199 89% 56% / .1);
    border: 1px solid hsl(199 89% 56% / .26);
    color: hsl(193 90% 70%);
    font-size: 10.5px; letter-spacing: .04em;
  }
  h1 { margin: 16px 0 6px; font-size: 21px; font-weight: 600; letter-spacing: -.2px; }
  .sub { margin: 0 0 24px; font-size: 12px; line-height: 1.65; color: var(--muted); }

  .field { margin-bottom: 14px; }
  .field > label {
    display: block; margin-bottom: 7px;
    font-size: 11.5px; color: var(--fg-dim); letter-spacing: .02em;
  }
  .control { position: relative; display: flex; align-items: center; }
  .control .lead {
    position: absolute; left: 11px; display: grid; place-items: center;
    color: hsl(210 10% 46%); pointer-events: none;
  }
  input {
    width: 100%; height: 40px;
    padding: 0 12px 0 34px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--input);
    color: var(--fg);
    font-family: var(--font); font-size: 13px;
    outline: none;
    transition: border-color .15s ease, box-shadow .15s ease, background .15s ease;
  }
  input::placeholder { color: hsl(210 10% 40%); }
  input:hover { border-color: var(--border-strong); }
  input:focus {
    border-color: var(--primary);
    background: hsl(210 18% 17%);
    box-shadow: 0 0 0 3px hsl(199 89% 56% / .16);
  }
  .control.has-toggle input { padding-right: 42px; }
  .toggle {
    position: absolute; right: 6px;
    width: 30px; height: 30px; border: 0; border-radius: 6px;
    background: transparent; color: hsl(210 10% 50%);
    display: grid; place-items: center; cursor: pointer;
    transition: color .15s ease, background .15s ease;
  }
  .toggle:hover { color: var(--fg-dim); background: hsl(210 16% 22%); }

  .submit {
    width: 100%; height: 40px; margin-top: 6px;
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    border: 0; border-radius: 8px;
    background: linear-gradient(180deg, hsl(197 92% 60%) 0%, hsl(203 92% 50%) 100%);
    color: hsl(210 45% 7%);
    font-family: var(--font); font-size: 13px; font-weight: 650; letter-spacing: .3px;
    cursor: pointer;
    box-shadow: 0 8px 20px -12px hsl(199 89% 56% / 1), 0 1px 0 hsl(0 0% 100% / .22) inset;
    transition: transform .12s ease, filter .15s ease, box-shadow .15s ease;
  }
  .submit:hover { filter: brightness(1.06); box-shadow: 0 12px 26px -12px hsl(199 89% 56% / 1), 0 1px 0 hsl(0 0% 100% / .26) inset; }
  .submit:active { transform: translateY(1px); }
  .submit:focus-visible { outline: 2px solid hsl(199 89% 56% / .6); outline-offset: 2px; }
  .submit svg { opacity: .75; }

  .err {
    display: flex; align-items: center; gap: 7px;
    margin: 0 0 16px; padding: 9px 11px;
    border-radius: 8px;
    background: hsl(0 72% 58% / .1);
    border: 1px solid hsl(0 72% 58% / .3);
    color: hsl(0 72% 76%);
    font-size: 12px;
    animation: shake .28s ease;
  }
  @keyframes shake {
    0%, 100% { transform: translateX(0); }
    25% { transform: translateX(-3px); }
    75% { transform: translateX(3px); }
  }

  .legal {
    margin: 20px 0 0;
    padding-top: 16px;
    border-top: 1px solid hsl(210 12% 16%);
    font-size: 10.5px; line-height: 1.7; color: hsl(210 10% 42%);
  }
  .legal b { color: hsl(210 10% 56%); font-weight: 500; }

  @media (max-width: 880px) {
    .shell { grid-template-columns: 1fr; max-width: 430px; }
    .brand { padding: 22px 24px; gap: 14px; border-right: 0; border-bottom: 1px solid var(--border); }
    .stage, .points { display: none; }
    .brand-foot { display: none; }
    .auth { padding: 26px 24px 24px; }
    h1 { font-size: 19px; }
  }
  @media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
  <div class="shell">
    <section class="brand">
      <div class="brand-head">
        <div class="mark" aria-hidden="true">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <circle cx="12" cy="12" r="7.5" stroke-dasharray="2.4 2.6"/>
            <path d="M12 3.2v4.4M12 16.4v4.4M3.2 12h4.4M16.4 12h4.4"/>
            <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>
          </svg>
        </div>
        <div>
          <b>医学影像阅片与手术规划</b>
          <span>MIIS · Imaging Suite</span>
        </div>
      </div>

      <div class="stage">
        <svg viewBox="0 0 452 214" role="img"
             aria-label="多平面重建视图示意：轴位、冠状、矢状三视口，含联动定位线、重建扫描线与层位标尺">
          <defs>
            <linearGradient id="paneBg" x1="0" y1="0" x2=".6" y2="1">
              <stop offset="0" stop-color="hsl(206 34% 15%)"/>
              <stop offset="1" stop-color="hsl(210 30% 8.5%)"/>
            </linearGradient>
            <radialGradient id="tissue" cx="50%" cy="46%" r="52%">
              <stop offset="0" stop-color="hsl(196 62% 62% / .5)"/>
              <stop offset=".55" stop-color="hsl(201 52% 42% / .28)"/>
              <stop offset="1" stop-color="hsl(205 45% 22% / 0)"/>
            </radialGradient>
            <linearGradient id="bone" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="hsl(196 80% 74% / .85)"/>
              <stop offset="1" stop-color="hsl(206 70% 55% / .35)"/>
            </linearGradient>
            <linearGradient id="scanTrail" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="hsl(190 95% 72% / 0)"/>
              <stop offset=".72" stop-color="hsl(190 95% 72% / .09)"/>
              <stop offset="1" stop-color="hsl(190 95% 72% / .22)"/>
            </linearGradient>
            <clipPath id="cpAxial"><rect x="14" y="42" width="136" height="130" rx="7"/></clipPath>
            <clipPath id="cpCoronal"><rect x="158" y="42" width="136" height="130" rx="7"/></clipPath>
            <clipPath id="cpSagittal"><rect x="302" y="42" width="136" height="130" rx="7"/></clipPath>
          </defs>

          <rect x=".5" y=".5" width="451" height="213" rx="11"
                fill="hsl(210 26% 8%)" stroke="hsl(210 12% 21%)"/>
          <path d="M0 30h452" stroke="hsl(210 12% 20%)"/>
          <circle cx="15" cy="15" r="2.6" fill="hsl(210 10% 30%)"/>
          <circle cx="25" cy="15" r="2.6" fill="hsl(210 10% 30%)"/>
          <circle cx="35" cy="15" r="2.6" fill="hsl(210 10% 30%)"/>
          <text x="50" y="18.5" fill="hsl(210 10% 48%)"
                font-family="'SFMono-Regular', Menlo, monospace" font-size="9" letter-spacing="1.2">MPR · LINKED</text>
          <circle class="live-dot" cx="130" cy="15" r="2" fill="hsl(152 62% 55%)"/>
          <text x="438" y="18.5" text-anchor="end" fill="hsl(210 10% 46%)"
                font-family="'SFMono-Regular', Menlo, monospace" font-size="9">512×512×107</text>

          <!-- 轴位 -->
          <g>
            <rect x="14" y="42" width="136" height="130" rx="7" fill="url(#paneBg)" stroke="hsl(210 12% 20%)"/>
            <g clip-path="url(#cpAxial)">
              <g class="tissue">
                <ellipse cx="82" cy="104" rx="52" ry="45" fill="url(#tissue)"/>
                <ellipse cx="82" cy="104" rx="40" ry="34" fill="none" stroke="url(#bone)" stroke-width="1.2" opacity=".7"/>
                <ellipse cx="64" cy="106" rx="7" ry="9" fill="hsl(196 70% 70% / .08)"/>
                <ellipse cx="100" cy="106" rx="7" ry="9" fill="hsl(196 70% 70% / .08)"/>
                <ellipse cx="82" cy="112" rx="12" ry="8" fill="hsl(196 60% 60% / .1)"/>
              </g>
              <path class="mv-y" d="M10 104h144" stroke="hsl(190 90% 65% / .3)" stroke-width=".9"/>
              <path class="mv-x" d="M82 38v138" stroke="hsl(190 90% 65% / .3)" stroke-width=".9"/>
              <g class="scan">
                <rect x="14" y="18" width="136" height="24" fill="url(#scanTrail)"/>
                <rect x="14" y="40.8" width="136" height="1.2" fill="hsl(190 95% 80%)" opacity=".45"/>
              </g>
              <g class="mv-x"><g class="mv-y">
                <circle class="ping" cx="82" cy="104" r="2.4" fill="none" stroke="hsl(190 95% 74%)" stroke-width="1"/>
                <circle cx="82" cy="104" r="2.4" fill="hsl(190 95% 72%)"/>
              </g></g>
            </g>
            <rect x="143" y="46" width="3" height="122" rx="1.5" fill="hsl(210 14% 15%)"/>
            <g class="mv-y"><rect x="142.4" y="52" width="4.2" height="17" rx="2.1" fill="hsl(190 85% 62% / .7)"/></g>
            <text id="sliceLabel" x="146" y="167.5" text-anchor="end"
                  fill="hsl(190 60% 72% / .8)" font-family="'SFMono-Regular', Menlo, monospace"
                  font-size="8">SL 054</text>
          </g>

          <!-- 冠状 -->
          <g>
            <rect x="158" y="42" width="136" height="130" rx="7" fill="url(#paneBg)" stroke="hsl(210 12% 20%)"/>
            <g clip-path="url(#cpCoronal)">
              <g class="tissue tissue-b">
                <ellipse cx="226" cy="106" rx="46" ry="50" fill="url(#tissue)"/>
                <path d="M226 56c-20 6-28 26-28 50s8 44 28 50 28-26 28-50-8-44-28-50z"
                      fill="none" stroke="url(#bone)" stroke-width="1.2" opacity=".62"/>
                <rect x="214" y="86" width="24" height="30" rx="5"
                      fill="hsl(190 90% 70% / .1)" stroke="hsl(190 90% 70% / .3)" stroke-width=".8"/>
              </g>
              <path class="mv-y" d="M154 106h144" stroke="hsl(190 90% 65% / .26)" stroke-width=".9"/>
              <path class="mv-x" d="M226 38v138" stroke="hsl(190 90% 65% / .26)" stroke-width=".9"/>
              <g class="scan scan-b">
                <rect x="158" y="18" width="136" height="24" fill="url(#scanTrail)"/>
                <rect x="158" y="40.8" width="136" height="1.2" fill="hsl(190 95% 80%)" opacity=".4"/>
              </g>
              <g class="mv-x"><g class="mv-y">
                <circle class="ping" cx="226" cy="106" r="2.4" fill="none" stroke="hsl(190 95% 74%)" stroke-width="1"/>
                <circle cx="226" cy="106" r="2.4" fill="hsl(190 95% 72%)"/>
              </g></g>
            </g>
            <rect x="287" y="46" width="3" height="122" rx="1.5" fill="hsl(210 14% 15%)"/>
            <g class="mv-y"><rect x="286.4" y="52" width="4.2" height="17" rx="2.1" fill="hsl(190 85% 62% / .7)"/></g>
          </g>

          <!-- 矢状 -->
          <g>
            <rect x="302" y="42" width="136" height="130" rx="7" fill="url(#paneBg)" stroke="hsl(210 12% 20%)"/>
            <g clip-path="url(#cpSagittal)">
              <g class="tissue tissue-c">
                <path d="M370 56c-24 0-38 22-38 50s14 50 38 50 34-22 34-50-10-50-34-50z" fill="url(#tissue)"/>
                <path d="M370 60c-18 4-26 22-26 46s8 42 26 46" fill="none" stroke="url(#bone)" stroke-width="1.2" opacity=".6"/>
              </g>
              <path class="mv-y" d="M298 106h144" stroke="hsl(190 90% 65% / .24)" stroke-width=".9"/>
              <path class="mv-z" d="M370 38v138" stroke="hsl(190 90% 65% / .24)" stroke-width=".9"/>
              <g class="scan scan-c">
                <rect x="302" y="18" width="136" height="24" fill="url(#scanTrail)"/>
                <rect x="302" y="40.8" width="136" height="1.2" fill="hsl(190 95% 80%)" opacity=".4"/>
              </g>
              <g class="mv-z"><g class="mv-y">
                <circle class="ping" cx="370" cy="106" r="2.4" fill="none" stroke="hsl(190 95% 74%)" stroke-width="1"/>
                <circle cx="370" cy="106" r="2.4" fill="hsl(190 95% 72%)"/>
              </g></g>
            </g>
            <rect x="431" y="46" width="3" height="122" rx="1.5" fill="hsl(210 14% 15%)"/>
            <g class="mv-y"><rect x="430.4" y="52" width="4.2" height="17" rx="2.1" fill="hsl(190 85% 62% / .7)"/></g>
          </g>

          <text x="22" y="166" fill="hsl(210 10% 50%)" font-size="9" letter-spacing=".6">AXIAL</text>
          <text x="166" y="166" fill="hsl(210 10% 50%)" font-size="9" letter-spacing=".6">CORONAL</text>
          <text x="310" y="166" fill="hsl(210 10% 50%)" font-size="9" letter-spacing=".6">SAGITTAL</text>

          <path d="M14 184h424" stroke="hsl(210 12% 17%)"/>
          <text x="14" y="201" fill="hsl(210 10% 40%)"
                font-family="'SFMono-Regular', Menlo, monospace" font-size="8.5">WW 400 · WL 40 · 1.0 mm</text>
          <circle class="live-dot" cx="404" cy="198.2" r="2.4" fill="hsl(152 62% 55%)"/>
          <text x="410" y="201" fill="hsl(190 60% 58%)"
                font-family="'SFMono-Regular', Menlo, monospace" font-size="8.5">SYNC</text>
        </svg>
        <div class="stage-cap">
          <span>同一坐标系下的三向定位</span>
          <code>LPS · patient_lps_mm</code>
        </div>
      </div>

      <ul class="points">
        <li>
          <span class="ico" aria-hidden="true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/>
            </svg>
          </span>
          <div>
            <b>多平面重建</b>
            <p>轴位 / 冠状 / 矢状联动，十字线定位同步到毫米级</p>
          </div>
        </li>
        <li>
          <span class="ico" aria-hidden="true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3l7 4v6c0 4-3 6.5-7 8-4-1.5-7-4-7-8V7l7-4z"/><path d="M9.5 12l1.8 1.8L15 10"/>
            </svg>
          </span>
          <div>
            <b>测量与分割</b>
            <p>病灶勾画、体积统计、段版本留痕，SEG 全程可追溯</p>
          </div>
        </li>
        <li>
          <span class="ico" aria-hidden="true">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 19V6a2 2 0 012-2h9l5 5v10a2 2 0 01-2 2H6a2 2 0 01-2-2z"/><path d="M14 4v5h5"/><path d="M8 13h7M8 16.5h4"/>
            </svg>
          </span>
          <div>
            <b>规划与报告</b>
            <p>穿刺路径、风险分级、结构化报告一次成型</p>
          </div>
        </li>
      </ul>

      <div class="brand-foot">
        <span class="tag">DICOM SR</span>
        <span class="tag">SEG</span>
        <span class="tag">FHIR R4</span>
        <span>DICOM 为唯一事实源</span>
      </div>
    </section>

    <section class="auth">
      <div class="chip">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <rect x="4.5" y="10.5" width="15" height="9.5" rx="2.2"/><path d="M8 10.5V7.8a4 4 0 018 0v2.7"/>
        </svg>
        受限访问
      </div>
      <h1>登录工作台</h1>
      <p class="sub">本站为演示环境，仅授权账号可查看影像与病例数据。</p>
      __ERROR__
      <form method="post" action="/login" autocomplete="on">
        <input type="hidden" name="next" value="__NEXT__" />
        <div class="field">
          <label for="username">用户名</label>
          <div class="control">
            <span class="lead" aria-hidden="true">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
                <circle cx="12" cy="8.2" r="3.6"/><path d="M4.8 20c.6-3.8 3.7-6 7.2-6s6.6 2.2 7.2 6"/>
              </svg>
            </span>
            <input id="username" name="username" placeholder="请输入账号" autocomplete="username"
                   autocapitalize="none" spellcheck="false" required autofocus />
          </div>
        </div>
        <div class="field">
          <label for="password">密码</label>
          <div class="control has-toggle">
            <span class="lead" aria-hidden="true">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">
                <rect x="4.5" y="10.5" width="15" height="9.5" rx="2.2"/><path d="M8 10.5V7.8a4 4 0 018 0v2.7"/>
              </svg>
            </span>
            <input id="password" name="password" type="password" placeholder="请输入密码"
                   autocomplete="current-password" required />
            <button type="button" class="toggle" id="toggle" aria-label="显示密码" aria-pressed="false">
              <svg id="eye" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12z"/>
                <circle cx="12" cy="12" r="2.9"/>
              </svg>
            </button>
          </div>
        </div>
        <button type="submit" class="submit">
          进入工作台
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 12h13M13 6.5l5.5 5.5-5.5 5.5"/>
          </svg>
        </button>
      </form>
      <p class="legal">
        <b>会话有效期 7 天</b> · 连续 8 次输错将锁定 5 分钟<br />
        登录与关键操作均写入审计日志；忘记口令请联系系统管理员。
      </p>
    </section>
  </div>

<script>
  (function () {
    var btn = document.getElementById('toggle')
    var pwd = document.getElementById('password')
    var eye = document.getElementById('eye')
    if (!btn || !pwd) return
    btn.addEventListener('click', function () {
      var show = pwd.type === 'password'
      pwd.type = show ? 'text' : 'password'
      btn.setAttribute('aria-pressed', show ? 'true' : 'false')
      btn.setAttribute('aria-label', show ? '隐藏密码' : '显示密码')
      eye.style.opacity = show ? '0.95' : '1'
      pwd.focus()
    })
  })();

  /* 层号跟随轴位定位线：直接读 CSS 动画的实时位移，数字和图像永远同步 */
  (function () {
    var label = document.getElementById('sliceLabel')
    var line = document.querySelector('.stage .mv-y')
    if (!label || !line) return
    var last = -1
    function tick() {
      if (document.hidden) return
      var t = window.getComputedStyle(line).transform
      var m = t && t.indexOf('matrix(') === 0 ? t.slice(7, -1).split(',') : null
      var ty = m && m.length >= 6 ? parseFloat(m[5]) : 0
      if (!isFinite(ty)) ty = 0
      var n = Math.round(54 - ty * 2.6)
      n = n < 1 ? 1 : (n > 107 ? 107 : n)
      if (n !== last) {
        last = n
        label.textContent = 'SL ' + (n < 100 ? (n < 10 ? '00' : '0') : '') + n
      }
    }
    tick()
    setInterval(tick, 320)
  })();
</script>
</body>
</html>"""


def render_login(next_path: str, error: str = "") -> str:
    """渲染登录页。next 已由调用方做过安全校验（只允许站内相对路径）。"""
    if error:
        err_html = (
            '<p class="err"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="2.2" stroke-linecap="round">'
            '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.2M12 16.4v.2"/></svg>'
            f"{error}</p>"
        )
    else:
        err_html = ""
    return (
        _TEMPLATE.replace("__NEXT__", next_path)
        .replace("__ERROR__", err_html)
    )
