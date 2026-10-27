(() => {
  const root=document.querySelector('[data-event-calendar]'); if(!root)return;
  const venue=root.querySelector('#calendarVenue'), start=root.querySelector('#calendarStart'), end=root.querySelector('#calendarEnd');
  const formVenue=document.getElementById('venue_id'), formDate=document.getElementById('proposed_date');
  const formStart=document.getElementById('proposed_start_time'), formEnd=document.getElementById('proposed_end_time');
  const status=root.querySelector('[data-calendar-state]'), grid=root.querySelector('[data-calendar-days]');
  const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Manila',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  let month=today.slice(0,7), selected=null, data=null, pending=null;
  const labels={available:'Available',partial:'Some slots reserved',pending:'Pending reservation',booked:'Booked',unavailable:'Unavailable'};
  function showDay(day,apply=false){
    selected=day.date;
    root.querySelector('[data-calendar-selected]').textContent=day.date;
    root.querySelector('[data-calendar-message]').textContent=day.available ? 'This date has availability for the selected venue and time. Final availability is checked when you submit.' : 'This date or time is unavailable. Choose another time, venue or date.';
    const details=root.querySelector('[data-calendar-details]'); details.replaceChildren();
    day.details.forEach(item=>{
      const row=document.createElement('p');
      row.textContent=`${item.start.slice(11)}–${item.end.slice(11)} | ${item.status}${item.name ? ' | '+item.name : ''}`;
      if(item.id){const link=document.createElement('a');link.href=`/events/${item.id}`;link.textContent=' View request';row.append(link);}
      details.append(row);
    });
    if(!day.details.length)details.textContent='No reservations recorded for this date.';
    if(apply && day.available && formDate){formDate.value=day.date;formDate.dispatchEvent(new Event('change',{bubbles:true}));}
    grid.querySelectorAll('[data-date]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.date===selected)));
  }
  async function refresh(){
    if(pending)pending.abort();pending=new AbortController();
    grid.querySelectorAll('button').forEach(button=>button.disabled=true);
    status.textContent='Checking current availability…';
    if(!venue.value){status.textContent='No active venue is available. Contact the Administrator.';return;}
    const params=new URLSearchParams({month,venue_id:venue.value});
    if(start.value || end.value){params.set('start_time',start.value);params.set('end_time',end.value);}
    try{
      const response=await fetch(root.dataset.url+'?'+params,{signal:pending.signal,cache:'no-store'});
      if(!response.ok)throw new Error('Choose both times with an end time after the start time, then refresh.');
      data=await response.json();grid.replaceChildren();
      root.querySelector('[data-calendar-month]').textContent=new Date(month+'-01T12:00:00').toLocaleDateString('en',{month:'long',year:'numeric'});
      for(let i=0;i<data.weekday;i++)grid.append(document.createElement('span'));
      data.days.forEach(day=>{
        const button=document.createElement('button');button.type='button';button.className='calendar-day '+day.state;button.dataset.date=day.date;
        button.setAttribute('aria-label',`${day.date}: ${labels[day.state]}`);button.setAttribute('aria-pressed',String(day.date===selected));
        const number=document.createElement('strong');number.textContent=day.day;
        const label=document.createElement('small');label.textContent=labels[day.state];button.append(number,label);
        if(day.pending){const count=document.createElement('small');count.textContent=`${day.pending} pending`;button.append(count);}
        button.addEventListener('click',()=>showDay(day,true));grid.append(button);
      });
      status.textContent='Availability refreshed. Automatically checked every 30 seconds.';
      const active=data.days.find(day=>day.date===selected);if(active)showDay(active);
    }catch(error){if(error.name!=='AbortError')status.textContent=error.message+' Calendar choices are paused until refreshed.';}
  }
  function navigate(delta){const date=new Date(month+'-01T12:00:00');date.setMonth(date.getMonth()+delta);const year=date.getFullYear();if(year<1900||year>9998)return;month=`${year}-${String(date.getMonth()+1).padStart(2,'0')}`;refresh();}
  root.querySelector('[data-calendar-prev]').addEventListener('click',()=>navigate(-1));
  root.querySelector('[data-calendar-next]').addEventListener('click',()=>navigate(1));
  root.querySelector('[data-calendar-refresh]').addEventListener('click',refresh);
  [venue,start,end].forEach(field=>field.addEventListener('change',()=>{if(formVenue)formVenue.value=venue.value;if(formStart)formStart.value=start.value;if(formEnd)formEnd.value=end.value;refresh();}));
  [[formVenue,venue],[formStart,start],[formEnd,end]].forEach(([input,control])=>{if(input)input.addEventListener('change',()=>{control.value=input.value;refresh();});});
  if(formDate)formDate.addEventListener('change',()=>{selected=formDate.value;if(selected){month=selected.slice(0,7);refresh();}});
  if(formVenue && venue.value)formVenue.value=venue.value;
  window.addEventListener('focus',refresh);setInterval(()=>{if(!document.hidden)refresh();},30000);refresh();
})();
