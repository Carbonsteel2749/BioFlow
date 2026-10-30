/* Functional motion only: owned by the mounted live task, never a progress clock. */
(function(){
 'use strict';
 let surface=null,tweens=[];
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 function stop(){tweens.forEach(t=>t.kill());tweens=[];surface?.querySelectorAll('[data-uw-flow]').forEach(n=>n.style.opacity='0');}
 function play(){stop();if(!surface?.isConnected||document.hidden||reduced.matches||!window.gsap)return;
  surface.querySelectorAll('[data-uw-flow]').forEach(dot=>{const path=dot.previousElementSibling;if(!path?.getTotalLength)return;const length=path.getTotalLength(),p={value:0};dot.style.opacity='1';
   tweens.push(gsap.to(p,{value:1,duration:1.7,ease:'none',repeat:-1,onUpdate:()=>{const at=path.getPointAtLength(length*p.value);gsap.set(dot,{attr:{cx:at.x,cy:at.y}});}}));
  });
 }
 reduced.addEventListener('change',play);document.addEventListener('visibilitychange',play);
 window.BioFlowMotion={mount:el=>{stop();surface=el;play();},stop:()=>{stop();surface=null;},activeCount:()=>tweens.length};
})();
