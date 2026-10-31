// assets/charts.js — 图表配置（数据均可在正文表格中溯源）
(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var warn = style.getPropertyValue('--warn').trim();
  var bad = style.getPropertyValue('--bad').trim();
  var fontSans = style.getPropertyValue('--font-sans').trim();

  var isNarrow = window.innerWidth <= 700;
  var nonZero = function (p) { return p.value > 0 ? p.value : ''; };

  /* ---------- 图 1：各模块功能落地方式分布 ---------- */
  var elCoverage = document.getElementById('chart-coverage');
  var coverage = null;
  if (elCoverage) {
    coverage = echarts.init(elCoverage, null, { renderer: 'svg' });
    coverage.setOption({
      animation: false,
      grid: { left: 8, right: 28, top: 16, bottom: isNarrow ? 62 : 40, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        appendToBody: true,
        backgroundColor: bg2,
        borderColor: rule,
        textStyle: { color: ink, fontFamily: fontSans, fontSize: 12 },
        formatter: function (params) {
          var total = 0;
          var lines = params.map(function (p) {
            total += p.value;
            return p.marker + p.seriesName + '：<strong>' + p.value + '</strong> 项';
          });
          return '<div style="font-weight:700;margin-bottom:4px">' + params[0].name + '（共 ' + total + ' 项）</div>' + lines.join('<br>');
        }
      },
      legend: {
        bottom: 0,
        left: 'center',
        itemWidth: 10,
        itemHeight: 10,
        itemGap: 14,
        textStyle: { color: muted, fontFamily: fontSans, fontSize: 12 },
        data: ['开箱即用', '配置集成', '需二次开发']
      },
      xAxis: {
        type: 'value',
        minInterval: 1,
        max: 8,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: muted, fontFamily: fontSans, fontSize: 11 },
        splitLine: { lineStyle: { color: rule, type: 'dashed' } }
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: ['数据接入与集成', '二维阅片与三维重建', '分割与手术规划', '报告输出与远程协作'],
        axisLine: { lineStyle: { color: rule } },
        axisTick: { show: false },
        axisLabel: { color: ink, fontFamily: fontSans, fontSize: 12.5 }
      },
      series: [
        {
          name: '开箱即用',
          type: 'bar',
          stack: 'total',
          barWidth: 26,
          itemStyle: { color: accent },
          label: { show: true, formatter: nonZero, color: '#FFFFFF', fontFamily: fontSans, fontSize: 11, fontWeight: 700 },
          data: [4, 3, 4, 2]
        },
        {
          name: '配置集成',
          type: 'bar',
          stack: 'total',
          barWidth: 26,
          itemStyle: { color: accent2 },
          label: { show: true, formatter: nonZero, color: '#FFFFFF', fontFamily: fontSans, fontSize: 11, fontWeight: 700 },
          data: [1, 2, 2, 2]
        },
        {
          name: '需二次开发',
          type: 'bar',
          stack: 'total',
          barWidth: 26,
          itemStyle: { color: warn },
          label: { show: true, formatter: nonZero, color: '#FFFFFF', fontFamily: fontSans, fontSize: 11, fontWeight: 700 },
          data: [0, 0, 0, 1]
        }
      ]
    });
    window.addEventListener('resize', function () { coverage.resize(); });
  }

  /* ---------- 图 2：核心组件五维适配度 ---------- */
  var elRadar = document.getElementById('chart-radar');
  if (elRadar) {
    var radar = echarts.init(elRadar, null, { renderer: 'svg' });
    radar.setOption({
      animation: false,
      color: [accent, accent2, '#B08A3E', muted],
      tooltip: {
        trigger: 'item',
        appendToBody: true,
        backgroundColor: bg2,
        borderColor: rule,
        textStyle: { color: ink, fontFamily: fontSans, fontSize: 12 }
      },
      legend: {
        bottom: 0,
        left: 'center',
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: muted, fontFamily: fontSans, fontSize: 12 }
      },
      radar: {
        center: ['50%', isNarrow ? '46%' : '47%'],
        radius: isNarrow ? '46%' : '64%',
        splitNumber: 5,
        axisName: {
          color: ink,
          fontFamily: fontSans,
          fontSize: isNarrow ? 10 : 12,
          lineHeight: isNarrow ? 13 : 16
        },
        axisLine: { lineStyle: { color: rule } },
        splitLine: { lineStyle: { color: rule } },
        splitArea: { areaStyle: { color: ['#FFFFFF', '#F7FAFA'] } },
        indicator: [
          { name: '功能覆盖', max: 5 },
          { name: '社区与生态', max: 5 },
          { name: '文档与上手', max: 5 },
          { name: '生产验证', max: 5 },
          { name: isNarrow ? '集成成本\n（越高越易）' : '集成成本（越高越易）', max: 5 }
        ]
      },
      series: [{
        type: 'radar',
        symbolSize: 4,
        lineStyle: { width: 1.6 },
        areaStyle: { opacity: 0.08 },
        emphasis: { lineStyle: { width: 2.4 }, areaStyle: { opacity: 0.16 } },
        data: [
          { value: [5, 5, 4, 5, 4], name: 'OHIF Viewer' },
          { value: [5, 4, 4, 4, 3], name: 'Cornerstone3D' },
          { value: [5, 5, 4, 5, 2], name: '3D Slicer' },
          { value: [3, 4, 5, 4, 5], name: 'Orthanc' }
        ]
      }]
    });
    window.addEventListener('resize', function () { radar.resize(); });
  }

  /* ---------- 图 3：风险分布矩阵 ---------- */
  var elRisk = document.getElementById('chart-risk');
  if (elRisk) {
    var risk = echarts.init(elRisk, null, { renderer: 'svg' });
    var riskNames = {
      R1: 'R1 实时协作自研，工作量与体验不确定',
      R2: 'R2 器械属性判定改变项目性质',
      R3: 'R3 双栈导致维护成本上升',
      R4: 'R4 开源许可与模型权重条款',
      R5: 'R5 上游版本演进或维护失活',
      R6: 'R6 Web 端大序列性能瓶颈',
      R7: 'R7 PACS / HIS 厂商配合不足',
      R8: 'R8 患者数据泄露或越权访问'
    };
    var riskData = [
      { code: 'R1', value: [3, 5], tip: riskNames.R1, impact: 3, likelihood: 5, size: 50, color: bad },
      { code: 'R2·R8', value: [5, 3], tip: riskNames.R2 + '<br>' + riskNames.R8 + '<br><span style="color:#5B6B72">两项评估坐标重合，合并显示</span>', impact: 5, likelihood: 3, size: 56, color: bad },
      { code: 'R3', value: [3, 4], tip: riskNames.R3, impact: 3, likelihood: 4, size: 44, color: warn },
      { code: 'R4', value: [4, 3], tip: riskNames.R4, impact: 4, likelihood: 3, size: 44, color: warn },
      { code: 'R5', value: [2, 3], tip: riskNames.R5, impact: 2, likelihood: 3, size: 34, color: accent },
      { code: 'R6', value: [3, 3], tip: riskNames.R6, impact: 3, likelihood: 3, size: 38, color: warn },
      { code: 'R7', value: [4, 4], tip: riskNames.R7, impact: 4, likelihood: 4, size: 54, color: bad }
    ];

    risk.setOption({
      animation: false,
      grid: { left: 20, right: 20, top: 30, bottom: 46, containLabel: true },
      tooltip: {
        trigger: 'item',
        appendToBody: true,
        backgroundColor: bg2,
        borderColor: rule,
        textStyle: { color: ink, fontFamily: fontSans, fontSize: 12 },
        formatter: function (p) {
          var d = p.data;
          return '<div style="font-weight:700;margin-bottom:4px">' + d.tip + '</div>' +
            '影响程度：' + d.impact + ' / 5<br>' +
            '发生可能性：' + d.likelihood + ' / 5<br>' +
            '综合指数：' + (d.impact * d.likelihood);
        }
      },
      xAxis: {
        type: 'value',
        name: '影响程度 →',
        nameLocation: 'middle',
        nameGap: 28,
        nameTextStyle: { color: muted, fontFamily: fontSans, fontSize: 11.5 },
        min: 0.5,
        max: 5.5,
        interval: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: muted, fontFamily: fontSans, fontSize: 11 },
        splitLine: { show: false }
      },
      yAxis: {
        type: 'value',
        name: '发生可能性 →',
        nameLocation: 'middle',
        nameGap: 32,
        nameTextStyle: { color: muted, fontFamily: fontSans, fontSize: 11.5 },
        min: 0.5,
        max: 5.5,
        interval: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: muted, fontFamily: fontSans, fontSize: 11 },
        splitLine: { lineStyle: { color: rule, type: 'dashed' } }
      },
      series: [{
        type: 'scatter',
        symbolSize: function (val, params) {
          return (params && params.data && params.data.size) || 40;
        },
        label: {
          show: true,
          position: 'inside',
          formatter: function (p) { return p.data.code; },
          color: '#FFFFFF',
          fontFamily: fontSans,
          fontSize: 10.5,
          fontWeight: 700
        },
        itemStyle: {
          color: function (p) { return p.data.color; },
          borderColor: '#FFFFFF',
          borderWidth: 1.5
        },
        data: riskData,
        markArea: {
          silent: true,
          label: { show: true, position: 'insideTopLeft', color: muted, fontFamily: fontSans, fontSize: 11 },
          data: [
            [
              { name: '重点管控', xAxis: 3, yAxis: 3, itemStyle: { color: 'rgba(156,59,46,0.05)' } },
              { xAxis: 5.5, yAxis: 5.5 }
            ],
            [
              { xAxis: 0.5, yAxis: 3, itemStyle: { color: 'rgba(180,99,58,0.035)' } },
              { xAxis: 3, yAxis: 5.5 }
            ]
          ]
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: rule, type: 'dashed', width: 1 },
          label: { show: false },
          data: [{ xAxis: 3 }, { yAxis: 3 }]
        }
      }]
    });
    window.addEventListener('resize', function () { risk.resize(); });
  }
})();
