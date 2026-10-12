
'use strict';
var fs = require('fs');
// ---- 页面全局桩 ----
var COLS = 'ABCDEFGHJKLMNOPQRST';
var boardSize = 19;
var RANK_COLORS = ['#1B5E20','#2E7D32','#4CAF50','#81C784','#A5D6A7'];
var PV_MAX_SHOW = 12;
var margin = 30, cellSize = 30;
var analyzeResults = [];
var analyzeNextColor = 'B';
var hoveredPVIndex = -1;
var pinnedPVIndex = -1;
var currentTab = 'analyze';
var statusHtml = '';
var nextPlayerElValue = 'B';
var document = {
  getElementById: function(id) {
    if (id === 'analyze-status') return { get innerHTML(){return statusHtml;}, set innerHTML(v){statusHtml = v;} };
    if (id === 'next-player') return { get value(){return nextPlayerElValue;}, set value(v){nextPlayerElValue = v;} };
    return {};
  }
};
var drawBoardCalls = 0;
function drawBoard(){ drawBoardCalls++; }

function makeCtx(){
  var log = [];
  var ctx = {
    globalAlpha: 1, fillStyle: '', strokeStyle: '', lineWidth: 0,
    textAlign: '', textBaseline: '', font: '',
    beginPath: function(){ log.push(['beginPath']); },
    arc: function(x,y,r){ log.push(['arc', x, y, r]); },
    fill: function(){ log.push(['fill', ctx.fillStyle, ctx.globalAlpha]); },
    stroke: function(){ log.push(['stroke', ctx.strokeStyle, ctx.globalAlpha]); },
    fillText: function(t,x,y){ log.push(['fillText', String(t), x, y]); }
  };
  ctx._log = log;
  return ctx;
}

function resetFixture(results, nextColor, tab){
  analyzeResults = results; analyzeNextColor = nextColor || 'B';
  currentTab = tab || 'analyze';
  hoveredPVIndex = -1; pinnedPVIndex = -1;
  statusHtml = ''; drawBoardCalls = 0;
  globalThis.ctx = makeCtx(); // 每次重置时换新 ctx 桩（块C等未显式建桩的绘制有默认可归集的桩）
}
function stdResults(){
  return [
    {move:'Q16', winrate:51.2, scoreLead:0.3, visits:400, pv:['Q16','D4','Q3','C17','R14']},
    {move:'D4',  winrate:49.8, scoreLead:-0.1, visits:380, pv:['D4','Q16','C3','R6','K10']},
    {move:'K10', winrate:48.9, scoreLead:-0.5, visits:300, pv:[]}
  ];
}
function pos(g){ return gtpToBoard(g); }

var results = [];
function T(name, cond, detail){
  results.push({name: name, pass: !!cond, detail: detail === undefined ? '' : String(detail)});
}

// ===== extracted: gtpToBoard =====
function gtpToBoard(gtp) {
    if (!gtp || gtp.toLowerCase()==='pass' || gtp.toLowerCase()==='resign') return null;
    var c = COLS.indexOf(gtp[0].toUpperCase());
    var r = boardSize - parseInt(gtp.slice(1));
    if (c < 0 || r < 0 || r >= boardSize) return null;
    return [r, c];
}

// ===== extracted: togglePinnedPV =====
function togglePinnedPV(row, col) {
    var idx = -1;
    for (var i=0;i<analyzeResults.length;i++) {
        var rp = gtpToBoard(analyzeResults[i].move);
        if (rp && rp[0]===row && rp[1]===col) { idx = i; break; }
    }
    if (idx < 0) return;
    // 点击当前已固定的项 → 取消；否则固定新项
    pinnedPVIndex = (pinnedPVIndex === idx) ? -1 : idx;
    hoveredPVIndex = -1;
    drawBoard();
    if (pinnedPVIndex >= 0) {
        var pv = analyzeResults[pinnedPVIndex].pv || [];
        if (pv.length === 0) {
            document.getElementById('analyze-status').innerHTML += '<br><span style="color:#e57373">⚠ 该候选点无后续变化数据</span>';
            return;
        }
        // 固定后立即绘制展开图（不等首次 mousemove）
        drawPVOverlay({pvMoves: pv, nextPlayer: analyzeNextColor});
        renderPvText(pinnedPVIndex);
    } else {
        renderAnalyzeStatusHtml();
    }
}

// ===== extracted: renderAnalyzeStatusHtml =====
function renderAnalyzeStatusHtml() {
    var html = '<b>Top '+analyzeResults.length+' 推荐走法</b> (轮'+(analyzeNextColor==='B'?'黑':'白')+'走)<br>';
    for (var i=0;i<analyzeResults.length;i++) {
        var rec = analyzeResults[i];
        var sign = rec.scoreLead >= 0 ? '+' : '';
        var rp = gtpToBoard(rec.move);
        var open = (rp && rec.pv && rec.pv.length>0)
            ? '<span style="cursor:pointer" onclick="togglePinnedPV('+rp[0]+','+rp[1]+')">'
            : '<span>';
        html += open + '<span style="color:'+RANK_COLORS[i]+'">&#9679;</span> ' +
            '<b>'+(i+1)+'. '+rec.move+'</b>  胜率 '+rec.winrate+'%  ' +
            '领先 '+sign+rec.scoreLead+'目  ('+rec.visits+'次模拟)</span><br>';
    }
    html += '<br><span style="color:#f8d76b">➔ 点击棋盘任意空位即可试下落子（黑白交替），再点「AI 分析」查看新局面推荐；点击推荐条目可展开/收起变化图</span>';
    document.getElementById('analyze-status').innerHTML = html;
}

// ===== extracted: renderPvText =====
function renderPvText(idx) {
    var rec = analyzeResults[idx];
    var pv = rec.pv || [];
    var color = analyzeNextColor;
    var seq = [];
    for (var i=0;i<pv.length && i<PV_MAX_SHOW;i++) {
        seq.push((color==='B'?'黑':'白')+':'+pv[i]);
        color = (color==='B')?'W':'B';
    }
    var html = '<b>「'+rec.move+'」后续变化（前 '+seq.length+' 手，点击推荐条目收起）</b><br>' +
        '<span style="color:'+RANK_COLORS[idx]+'">&#9679;</span> '+seq.join(' → ') +
        (pv.length > PV_MAX_SHOW ? ' → …（共 '+pv.length+' 手）' : '');
    document.getElementById('analyze-status').innerHTML = html;
}

// ===== extracted: clearPinnedPV =====
function clearPinnedPV() { pinnedPVIndex = -1; }

// ===== extracted: drawPVOverlay =====
function drawPVOverlay(pvData) {
    var pvMoves = pvData.pvMoves;
    var currentColor = pvData.nextPlayer; // 第一手是 nextPlayer 的颜色
    for (var i=0; i<pvMoves.length && i<PV_MAX_SHOW; i++) {
        var pos = gtpToBoard(pvMoves[i]);
        if (!pos) continue; // pass 等跳过
        var pr=pos[0], pc=pos[1];
        var px=margin+pc*cellSize, py=margin+pr*cellSize;
        var stoneColor = (currentColor==='B') ? 1 : 2;

        // 半透明棋子
        ctx.globalAlpha = 0.55;
        var rad = cellSize*0.42;
        ctx.beginPath(); ctx.arc(px, py, rad, 0, Math.PI*2);
        if (stoneColor===1) {
            ctx.fillStyle = '#222';
        } else {
            ctx.fillStyle = '#eee';
        }
        ctx.fill();
        ctx.strokeStyle = stoneColor===1 ? '#000' : '#999';
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.globalAlpha = 1.0;

        // 手数编号
        ctx.fillStyle = stoneColor===1 ? '#fff' : '#000';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.font = 'bold '+Math.round(cellSize*0.36)+'px sans-serif';
        ctx.fillText(String(i+1), px, py);

        // 交替颜色
        currentColor = (currentColor==='B') ? 'W' : 'B';
    }
    ctx.textBaseline = 'alphabetic';
}


// ===== A 坐标命中 =====
(function(){
  T('A1 gtpToBoard Q16 == [3,15]', JSON.stringify(gtpToBoard('Q16'))==='[3,15]', gtpToBoard('Q16'));
  T('A2 gtpToBoard D4 == [15,3]', JSON.stringify(gtpToBoard('D4'))==='[15,3]', gtpToBoard('D4'));
  T('A3 gtpToBoard K10 == [9,9]', JSON.stringify(gtpToBoard('K10'))==='[9,9]', gtpToBoard('K10'));
  T('A4 gtpToBoard pass == null', gtpToBoard('pass')===null);
  T('A5 gtpToBoard resign == null', gtpToBoard('resign')===null);
  T('A6 gtpToBoard A20 越界 == null', gtpToBoard('A20')===null);
  T('A7 gtpToBoard Z5 非法列 == null', gtpToBoard('Z5')===null);
})();

// ===== C togglePinnedPV 状态机三态 =====
(function(){
  // 态1: 未固定 -> 固定
  resetFixture(stdResults(), 'B');
  togglePinnedPV(3, 15);
  T('C1 点击推荐点固定 idx=0', pinnedPVIndex===0, 'pinned='+pinnedPVIndex);
  T('C2 固定时状态栏为变化序列', statusHtml.indexOf('「Q16」后续变化')>=0 && statusHtml.indexOf('点击推荐条目收起')>=0);
  T('C3 序列首手为黑(轮走方)', statusHtml.indexOf('黑:Q16')>=0);
  // 态2: 再点同一点 -> 取消
  togglePinnedPV(3, 15);
  T('C4 再点同一推荐点取消 pinned=-1', pinnedPVIndex===-1);
  T('C5 取消后恢复推荐列表', statusHtml.indexOf('Top 3 推荐走法')>=0 && statusHtml.indexOf('点击推荐条目可展开/收起变化图')>=0);
  // 切换: 固定 A -> 点推荐点 B -> 切换为 B
  togglePinnedPV(3, 15);
  togglePinnedPV(15, 3);
  T('C6 点其他推荐点切换 idx=1', pinnedPVIndex===1, 'pinned='+pinnedPVIndex);
  T('C7 切换后序列为新推荐点', statusHtml.indexOf('「D4」后续变化')>=0);
  // 点非推荐点坐标: togglePinnedPV 直接返回, 状态不变
  togglePinnedPV(0, 0);
  T('C8 点非推荐点状态不变', pinnedPVIndex===1, 'pinned='+pinnedPVIndex);
  // 固定时清除悬停
  hoveredPVIndex = 2;
  togglePinnedPV(9, 9); // K10 是第3个推荐点(pv空)
  T('C9 切换到第3推荐点', pinnedPVIndex===2);
  T('C10 固定后悬停索引清除', hoveredPVIndex===-1);
  togglePinnedPV(9, 9);
  T('C11 取消固定后可再次固定', (togglePinnedPV(3,15), pinnedPVIndex===0));
})();

// ===== D renderPvText 文字序列 =====
(function(){
  var longPv = [];
  for (var i=0;i<15;i++) longPv.push('K'+(10+i)); // 15 手
  var longResults = [{move:'Q16', winrate:50, scoreLead:0, visits:100, pv:longPv}];
  resetFixture(longResults, 'B');
  togglePinnedPV(3, 15);
  T('D1 黑先交替序列', statusHtml.indexOf('黑:K10')>=0 && statusHtml.indexOf('白:K11')>=0 && statusHtml.indexOf('黑:K12')>=0);
  T('D2 前12手标注', statusHtml.indexOf('前 12 手')>=0);
  T('D3 >12手截断显示总数', statusHtml.indexOf('…（共 15 手）')>=0);
  T('D4 第13手起不展示', statusHtml.indexOf('K22')<0 && statusHtml.indexOf('K23')<0 && statusHtml.indexOf('K24')<0);
  resetFixture(longResults, 'W');
  togglePinnedPV(3, 15);
  T('D5 白先首手=白', statusHtml.indexOf('白:K10')>=0 && statusHtml.indexOf('黑:K11')>=0);
  resetFixture([{move:'Q16', winrate:50, scoreLead:0, visits:100, pv:['Q16','D4']}], 'B');
  togglePinnedPV(3, 15);
  T('D6 短序列无截断标注', statusHtml.indexOf('共 ')<0 && statusHtml.indexOf('前 2 手')>=0);
})();

// ===== E pv 空分支 =====
(function(){
  resetFixture(stdResults(), 'B');
  togglePinnedPV(9, 9); // K10 pv=[]
  T('E1 pv空固定仍生效', pinnedPVIndex===2);
  T('E2 pv空提示无后续变化数据', statusHtml.indexOf('该候选点无后续变化数据')>=0, statusHtml.slice(0,120));
  // drawPVOverlay 空 pv 不画任何子
  var c0 = makeCtx(); globalThis.ctx = c0;
  drawPVOverlay({pvMoves: [], nextPlayer: 'B'});
  T('E3 空pv绘制0个元素', c0._log.filter(function(e){return e[0]==='arc';}).length===0);
})();

// ===== F drawPVOverlay 绘制逻辑（真实提取函数 + ctx 桩） =====
(function(){
  var c = makeCtx();
  globalThis.ctx = c;
  drawPVOverlay({pvMoves: ['Q16','D4','Q3'], nextPlayer: 'B'});
  var arcs = c._log.filter(function(e){return e[0]==='arc';});
  var fills = c._log.filter(function(e){return e[0]==='fill';});
  var labels = c._log.filter(function(e){return e[0]==='fillText';});
  T('F1 3手绘制3子', arcs.length===3, 'arcs='+arcs.length);
  T('F2 半透明0.55', fills.every(function(f){return Math.abs(f[2]-0.55)<1e-9;}));
  T('F3 黑白交替(黑先)', fills[0][1]==='#222' && fills[1][1]==='#eee' && fills[2][1]==='#222',
    fills.map(function(f){return f[1];}).join(','));
  T('F4 手数编号1..3', labels[0][1]==='1' && labels[1][1]==='2' && labels[2][1]==='3');
  T('F5 白先首子为白', (function(){ var c2=makeCtx(); globalThis.ctx=c2;
      drawPVOverlay({pvMoves:['D4'], nextPlayer:'W'});
      var f2=c2._log.filter(function(e){return e[0]==='fill';});
      return f2.length===1 && f2[0][1]==='#eee'; })());
  T('F6 pass跳过不绘制', (function(){ var c3=makeCtx(); globalThis.ctx=c3;
      drawPVOverlay({pvMoves:['Q16','pass','D4'], nextPlayer:'B'});
      var a3=c3._log.filter(function(e){return e[0]==='arc';});
      var l3=c3._log.filter(function(e){return e[0]==='fillText';});
      return a3.length===2 && l3[0][1]==='1' && l3[1][1]==='3'; })(),
    'pass 后编号顺延(实现行为)');
  T('F7 超上限截断(实现=PV_MAX_SHOW=12)', (function(){ var c4=makeCtx(); globalThis.ctx=c4;
      var pv15=[]; for(var i=0;i<15;i++) pv15.push('K'+(1+i)); // 15 个全部合法坐标
      drawPVOverlay({pvMoves:pv15, nextPlayer:'B'});
      return c4._log.filter(function(e){return e[0]==='arc';}).length===12; })());
})();

console.log(JSON.stringify(results));
