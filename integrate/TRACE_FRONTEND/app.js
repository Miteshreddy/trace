(function(){
  'use strict';
  const page=document.body.dataset.page;
  const $=s=>document.querySelector(s);
  const $$=s=>Array.from(document.querySelectorAll(s));
  function sleep(ms){return new Promise(r=>setTimeout(r,ms));}

  // Keep the original TRACE hero demo sequence and timing: prompt typing ->
  // compact state -> live console reveal -> activity/cursor progression.
  async function runHomeDemo(){
    const typed=$('#demoTyped'), consoleEl=$('#console'), command=$('#demoCommand'), state=$('#demoState'), status=$('#sessionStatus');
    if(!typed||!consoleEl||!command) return;
    const header=$('#headerStatus');
    const activities=$$('.activity-item');
    const steps=$$('.j-step');
    const cursor=$('#cursor');
    const label=$('#cursorLabel');
    const goal='Check the signup flow and identify accessibility issues';
    const path=[[28,34],[59,28],[72,58],[43,63],[68,52]];
    const labels=['Opening website','Inspecting navigation','Testing primary flow','Checking accessibility','Capturing evidence'];

    typed.textContent='';
    consoleEl.classList.remove('visible');
    command.classList.remove('compact');
    state.classList.remove('ready');
    state.innerHTML='<i></i> Ready';
    activities.forEach(a=>a.classList.remove('done','current'));
    steps.forEach((s,i)=>{s.classList.toggle('reached',i===0);s.classList.remove('current');});
    if(cursor){cursor.style.left='28%';cursor.style.top='34%';}
    if(label)label.textContent='Exploring interface';
    if(status)status.textContent='Preparing session';
    if(header){header.dataset.state='running';header.querySelector('span').textContent='RUNNING';}

    await sleep(500);
    for(const ch of goal){typed.textContent+=ch;await sleep(23)}
    await sleep(320);
    state.classList.add('ready');
    state.innerHTML='<i></i> Goal understood';
    command.classList.add('compact');
    await sleep(180);
    consoleEl.classList.add('visible');
    await sleep(420);
    if(status)status.textContent='Agent is browsing';

    for(let i=0;i<activities.length;i++){
      if(activities[i-1]) activities[i-1].classList.add('done');
      activities[i].classList.add('current');
      steps[Math.min(i+1,steps.length-1)].classList.add('current');
      steps[Math.min(i+1,steps.length-1)].classList.add('reached');
      if(cursor){cursor.style.left=path[i][0]+'%';cursor.style.top=path[i][1]+'%';}
      if(label)label.textContent=labels[i];
      await sleep(430);
      activities[i].classList.remove('current'); activities[i].classList.add('done');
      steps[Math.min(i+1,steps.length-1)].classList.remove('current');
    }
    if(status)status.textContent='Evidence captured';
    if(state){state.classList.add('ready');state.innerHTML='<i></i> Session active';}
    if(header){header.dataset.state='success';header.querySelector('span').textContent='EVIDENCE READY';}
  }

  function setupPointer(){
    const pointer=$('#tracePointer');
    if(!pointer || matchMedia('(pointer:coarse)').matches) return;
    let x=0,y=0,tx=0,ty=0,visible=false;
    window.addEventListener('mousemove',e=>{tx=e.clientX;ty=e.clientY;if(!visible){visible=true;pointer.style.opacity='1';}});
    window.addEventListener('mousedown',()=>pointer.classList.add('is-down'));
    window.addEventListener('mouseup',()=>pointer.classList.remove('is-down'));
    document.addEventListener('pointerover',e=>{const target=e.target.closest('a,button,.cap,.journey-node,.evidence-card-front,.glass-tag');pointer.classList.toggle('is-link',!!target)});
    const tick=()=>{x+=(tx-x)*.22;y+=(ty-y)*.22;pointer.style.left=x+'px';pointer.style.top=y+'px';requestAnimationFrame(tick)};tick();
  }

  function setupReveal(){
    const items=$$('.reveal-scroll');
    if(!items.length) return;
    const io=new IntersectionObserver(entries=>{entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add('in-view');io.unobserve(entry.target)}})},{threshold:.18});
    items.forEach(item=>io.observe(item));
  }

  function setupScrollMotion(){
    const evidence=$('.evidence-stage');
    const journey=$('#journeyVisual');
    if(!evidence && !journey) return;
    let raf=0;
    const update=()=>{
      raf=0;
      const y=window.scrollY||0;
      if(evidence){const r=evidence.getBoundingClientRect();const shift=(window.innerHeight*.5-(r.top+r.height*.5))*.08;evidence.style.setProperty('--scroll-shift',shift.toFixed(1)+'px');}
      if(journey){const r=journey.getBoundingClientRect();const n=Math.max(-1,Math.min(1,(window.innerHeight*.5-(r.top+r.height*.5))/window.innerHeight));journey.style.transform='translateY('+(n*4).toFixed(1)+'px)';}
    };
    window.addEventListener('scroll',()=>{if(!raf)raf=requestAnimationFrame(update)},{passive:true});
    update();
  }

  setupPointer();
  setupReveal();
  setupScrollMotion();

  if(page==='home'){
    const demoStage=$('#console');
    let started=false;
    const start=()=>{if(started)return;started=true;runHomeDemo().catch(()=>{const h=$('#headerStatus');if(h){h.dataset.state='failed';h.querySelector('span').textContent='FAILED';}});};
    if(demoStage){const io=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){start();io.disconnect();}},{threshold:.08});io.observe(demoStage);}
    $('#replay')?.addEventListener('click',()=>{started=false;runHomeDemo().catch(()=>{const h=$('#headerStatus');if(h){h.dataset.state='failed';h.querySelector('span').textContent='FAILED';}});started=true;});
  }

  if(page==='command'){
    const goal=$('#goal'), url=$('#targetUrl'), advanced=$('#advanced'), err=$('#error'), reach=$('#commandReach'), run=$('#runAudit');
    const storedGoal=sessionStorage.getItem('traceGoal'), storedUrl=sessionStorage.getItem('traceUrl');
    if(storedGoal&&goal)goal.value=storedGoal;if(storedUrl&&url)url.value=storedUrl;
    $('#advancedBtn')?.addEventListener('click',()=>advanced?.classList.toggle('show'));
    $$('.try button').forEach(b=>b.addEventListener('click',()=>{goal.value=b.textContent;err?.classList.remove('show');goal.focus()}));
    const updateReach=()=>{const value=(url?.value||'').trim();let ok=false;try{ok=!!new URL(value)}catch(e){ok=false}if(reach){reach.innerHTML=ok?'<i></i> Target ready':'<i style="background:#8f4f5a"></i> Check target';reach.style.color=ok?'#777984':'#b06e7a'}return ok;};
    url?.addEventListener('input',updateReach);updateReach();
    run?.addEventListener('click',async()=>{if(!goal?.value.trim()){err.textContent='Add a testing goal before starting a run.';err.classList.add('show');goal.focus();return;}if(!updateReach()){err.textContent='Enter a valid target URL, including http:// or https://.';err.classList.add('show');url.focus();return;}err.classList.remove('show');sessionStorage.setItem('traceGoal',goal.value.trim());sessionStorage.setItem('traceUrl',url.value.trim());run.disabled=true;run.innerHTML='Preparing…';const api=(window.TRACE_CONFIG&&window.TRACE_CONFIG.API_BASE_URL)||'';if(api){try{const response=await fetch(api.replace(/\/$/,'')+'/audit/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal:goal.value.trim(),target_url:url.value.trim(),max_steps:Number($('#maxSteps').value),exploration_passes:Number($('#passes').value)})});if(!response.ok)throw new Error('launch failed')}catch(e){err.textContent='The audit service could not be reached. Local Live Session preview is still available.';err.classList.add('show');run.disabled=false;run.innerHTML='Run Audit ↗';return;}}await sleep(350);run.innerHTML='Opening Live Session…';await sleep(500);location.href='live.html';});
  }

  if(page==='live'){
    const storedGoal=sessionStorage.getItem('traceGoal'),storedUrl=sessionStorage.getItem('traceUrl');
    if(storedGoal&&$('#liveGoal'))$('#liveGoal').textContent=storedGoal;if(storedGoal&&$('#journeyGoal'))$('#journeyGoal').textContent=storedGoal;
    const address=$('.live-browser .address');if(storedUrl&&address)address.textContent='⌁  '+storedUrl;
    const activities=$$('.live-console .activity-item'),steps=$$('.live-console .j-step'),cursor=$('#liveCursor');const path=[[29,34],[55,28],[69,57],[45,64],[70,52]];let index=0;
    const interval=setInterval(()=>{activities.forEach((a,i)=>{a.classList.toggle('done',i<index);a.classList.toggle('current',i===index);if(i===index)a.querySelector('i').textContent='◌';});steps.forEach((s,i)=>s.classList.toggle('reached',i<=index+2));if(cursor){cursor.style.left=path[index][0]+'%';cursor.style.top=path[index][1]+'%';}index=Math.min(index+1,4)},900);setTimeout(()=>clearInterval(interval),5500);
  }
})();
