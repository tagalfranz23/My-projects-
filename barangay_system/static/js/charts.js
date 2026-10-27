/* Charts use the same SQLite aggregates as the adjacent accessible data table. */
(() => {
  const palette = ['#082f5b','#d4a62a','#52758f','#b7c9d6','#8d793e','#596677','#ddd3b6','#99a7ac'];
  function drawDashboard(ctx, data, width, height, values, max) {
    const rows = data.rows;
    if (data.kind === 'bar') {
      rows.forEach((row,i) => {
        const y = 12 + i * 48;
        let label = String(row[0]);
        while (ctx.measureText(label).width > width - 42 && label.length > 1) label = label.slice(0,-2) + '…';
        ctx.fillStyle = '#334155'; ctx.textAlign = 'left'; ctx.fillText(label, 0, y + 10);
        ctx.textAlign = 'right'; ctx.fillStyle = palette[0]; ctx.fillText(values[i].toLocaleString('en-PH'), width - 3, y + 10);
        ctx.fillStyle = '#eaf1f8'; ctx.fillRect(0, y + 20, width - 3, 10);
        ctx.fillStyle = palette[i % palette.length]; ctx.fillRect(0, y + 20, (width - 3) * values[i] / max, 10);
      });
      return;
    }
    const left = 32, right = width - 16, top = 24, bottom = height - 35;
    const peak = Math.max(4, Math.ceil(max / 4) * 4);
    ctx.font = '11px Arial';
    for (let tick = 0; tick <= 4; tick++) {
      const y = bottom - tick / 4 * (bottom - top);
      ctx.strokeStyle = '#e6edf4'; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      ctx.fillStyle = '#64748b'; ctx.textAlign = 'right'; ctx.fillText((peak * tick / 4).toLocaleString('en-PH'), left - 7, y + 4);
    }
    const points = rows.map((row,i) => [left + i * (right-left) / Math.max(1,rows.length-1), bottom - values[i] / peak * (bottom-top)]);
    ctx.strokeStyle = palette[0]; ctx.lineWidth = 2.5; ctx.beginPath();
    points.forEach(([x,y],i) => { if(i) ctx.lineTo(x,y); else ctx.moveTo(x,y); });ctx.stroke();
    points.forEach(([x,y],i) => {
      ctx.beginPath();ctx.arc(x,y,3.5,0,Math.PI*2);ctx.fillStyle=palette[0];ctx.fill();
      if (i % 3 === 0 || i === rows.length-1) {
        ctx.fillStyle='#64748b';ctx.textAlign=i===rows.length-1?'right':'center';ctx.fillText(rows[i][0].slice(0,3),x,bottom+23);
      }
    });
  }
  function draw(canvas) {
    const data = JSON.parse(canvas.dataset.chart);
    const rows = data.rows;
    const width = data.dashboard ? Math.max(160, canvas.parentElement.clientWidth) : Math.max(260, canvas.parentElement.clientWidth - 40);
    const height = data.dashboard && data.kind === 'bar' ? Math.max(220, rows.length * 48 + 12) : data.kind === 'bar' ? Math.max(260, rows.length * 30 + 40) : 280;
    const scale = window.devicePixelRatio || 1;
    canvas.width = width * scale;
    canvas.height = height * scale;
    canvas.style.width = '100%'; canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d'); ctx.scale(scale, scale);
    ctx.font = '12px Arial';
    const values = rows.map(row => Number(row[1]));
    const max = Math.max(1, ...values), total = values.reduce((a,b) => a+b,0);
    if (!rows.length || !total) return;
    if (data.dashboard) { drawDashboard(ctx,data,width,height,values,max); return; }
    if (data.kind === 'stacked') {
      const pairs=rows.map(r=>r[0].split(' · ')),months=[...new Set(pairs.map(p=>p[0]))],statuses=[...new Set(pairs.map(p=>p[1]))];
      const counts=months.map(m=>statuses.map(s=>rows.reduce((sum,r,i)=>sum+(pairs[i][0]===m&&pairs[i][1]===s?Number(r[1]):0),0)));
      const peak=Math.max(1,...counts.map(v=>v.reduce((a,b)=>a+b,0)));
      counts.forEach((series,i)=>{let bottom=height-65;const step=(width-40)/months.length,x=20+i*step;series.forEach((v,j)=>{const h=v/peak*(height-100);ctx.fillStyle=palette[j%palette.length];ctx.fillRect(x,bottom-h,Math.max(2,step-12),h);bottom-=h});ctx.fillStyle=palette[0];ctx.textAlign='left';if(months.length<=8||i===0||i===months.length-1)ctx.fillText(months[i],x,height-40)});
      ctx.fillText('Status counts are listed below',20,height-15);
    } else if (data.kind === 'donut' && rows.length <= 8) {
      const radius = Math.min(90,width/4), cx = width/2, cy = 105;
      let angle = -Math.PI/2;
      rows.forEach((row,i) => {
        const end = angle + values[i]/total*Math.PI*2;
        ctx.beginPath();ctx.arc(cx,cy,radius,angle,end);ctx.arc(cx,cy,radius*0.55,end,angle,true);ctx.closePath();
        ctx.fillStyle=palette[i%palette.length];ctx.fill();angle=end;
      });
      ctx.fillStyle='#082f5b';ctx.textAlign='center';ctx.fillText('Total '+total,cx,cy+4);
      ctx.fillText('Category values are listed below',cx,height-20);
    } else if (data.kind === 'line') {
      ctx.strokeStyle=palette[0];ctx.lineWidth=2;ctx.beginPath();
      rows.forEach((row,i) => {const x=35+i*(width-70)/Math.max(1,rows.length-1),y=height-55-values[i]/max*(height-90);if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y)});ctx.stroke();
      rows.forEach((row,i)=>{const x=35+i*(width-70)/Math.max(1,rows.length-1),y=height-55-values[i]/max*(height-90);ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fillStyle=palette[0];ctx.fill();ctx.textAlign='center';ctx.fillText(values[i],x,y-9);if(rows.length<=8||i===0||i===rows.length-1)ctx.fillText(row[0],x,height-25)});
    } else {
      rows.forEach((row,i)=>{const y=20+i*(height-30)/rows.length;ctx.fillStyle='#082f5b';ctx.textAlign='left';ctx.fillText(String(row[0]).slice(0,24),0,y+12);ctx.fillStyle=palette[0];ctx.fillRect(width*0.43,y,(width*0.46)*values[i]/max,16);ctx.fillStyle='#082f5b';ctx.fillText(String(values[i]),width*0.91,y+12)});
    }
  }
  const redraw = (root = document) => root.querySelectorAll('.report-chart').forEach(draw);
  redraw();
  document.addEventListener('charts:refresh', event => redraw(event.detail || document));
  let timer;
  window.addEventListener('resize',()=>{clearTimeout(timer);timer=setTimeout(()=>redraw(),150)});
})();
