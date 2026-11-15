(function(){
  'use strict';

  const $=id=>document.getElementById(id);

  function installStyle(){
    if($('leshyForestThemeV2Style'))return;
    const style=document.createElement('style');
    style.id='leshyForestThemeV2Style';
    style.textContent=`
      /* Только визуальный слой. Структура, история чатов и обработчики меню не меняются. */
      body.leshy-theme-forest{--forest-moss:#769454;--forest-moss-dark:#30472d;--forest-lime:#a7d45e;--forest-root:#765b3b;--forest-glow:rgba(171,221,91,.28)}
      body.leshy-theme-forest .sidebar{
        background:
          radial-gradient(circle at 105% 18%,rgba(112,151,73,.11),transparent 32%),
          linear-gradient(180deg,rgba(4,15,9,.985),rgba(5,16,9,.975))!important;
        box-shadow:inset -1px 0 rgba(180,222,125,.10),8px 0 35px rgba(0,0,0,.18)!important
      }
      body.leshy-theme-forest .sidebar::after{content:"";position:absolute;pointer-events:none;right:-1px;top:0;bottom:0;width:2px;background:linear-gradient(180deg,transparent,rgba(159,211,96,.22) 18%,rgba(77,119,54,.12) 72%,transparent)}
      body.leshy-theme-forest #chatSidebarNew{border:1px solid transparent!important;transition:background .18s,border-color .18s,box-shadow .18s}
      body.leshy-theme-forest #chatSidebarNew:hover{border-color:rgba(167,217,103,.25)!important;box-shadow:inset 0 0 18px rgba(104,154,68,.08)}
      body.leshy-theme-forest #chatHistorySearch{background:rgba(17,28,21,.72)!important;border-color:rgba(141,181,105,.12)!important}
      body.leshy-theme-forest #sidebarHistoryTitle{color:#9eb08f!important}
      body.leshy-theme-forest #sidebarHistory #sessionList .item,
      body.leshy-theme-forest #sidebarHistory #sessionList .leshy-session-item{transition:background .15s,box-shadow .15s,color .15s}
      body.leshy-theme-forest #sidebarHistory #sessionList .item:hover,
      body.leshy-theme-forest #sidebarHistory #sessionList .leshy-session-item:hover{box-shadow:inset 2px 0 rgba(161,213,93,.36)!important}
      body.leshy-theme-forest #profileMenuButton{border-top:1px solid rgba(136,177,103,.08)!important}
      body.leshy-theme-forest #profileAvatar{background:linear-gradient(145deg,#3b7950,#1f4e34)!important;box-shadow:0 0 16px rgba(123,190,94,.16)}

      body.leshy-theme-forest #pane-chat .leshy-forest-v2-depth{position:absolute;z-index:-1;inset:0;overflow:hidden;pointer-events:none}
      body.leshy-theme-forest #pane-chat .leshy-forest-v2-depth::before{content:"";position:absolute;left:-7%;right:-7%;bottom:-55px;height:230px;opacity:.94;transform:translate(calc(var(--forest-x)*-1.65),calc(var(--forest-y)*-1.2));transition:transform .13s linear;background:
        radial-gradient(ellipse at 4% 100%,rgba(8,31,17,.98) 0 17%,transparent 37%),
        radial-gradient(ellipse at 18% 106%,rgba(27,57,29,.94) 0 12%,transparent 29%),
        radial-gradient(ellipse at 38% 110%,rgba(17,44,23,.92) 0 15%,transparent 31%),
        radial-gradient(ellipse at 63% 108%,rgba(18,47,24,.92) 0 16%,transparent 32%),
        radial-gradient(ellipse at 83% 105%,rgba(31,61,30,.94) 0 13%,transparent 30%),
        radial-gradient(ellipse at 99% 100%,rgba(9,31,17,.98) 0 19%,transparent 39%)}
      body.leshy-theme-forest #pane-chat .leshy-forest-v2-depth::after{content:"";position:absolute;inset:0;opacity:.32;background:
        radial-gradient(circle at 13% 79%,rgba(208,231,151,.13) 0 1px,transparent 2px),
        radial-gradient(circle at 28% 66%,rgba(235,243,170,.12) 0 1px,transparent 2px),
        radial-gradient(circle at 73% 75%,rgba(219,239,143,.12) 0 1px,transparent 2px),
        radial-gradient(circle at 88% 58%,rgba(222,239,159,.11) 0 1px,transparent 2px);background-size:94px 83px,131px 111px,117px 97px,157px 123px}

      body.leshy-theme-forest #pane-chat .leshy-forest-ground{position:absolute;z-index:2;left:0;right:0;bottom:0;height:104px;pointer-events:none;opacity:.84;background:
        linear-gradient(180deg,transparent 0,rgba(5,18,9,.17) 32%,rgba(5,17,9,.86) 100%),
        repeating-radial-gradient(ellipse at 7% 100%,transparent 0 18px,rgba(79,108,54,.30) 20px 23px,transparent 25px 43px)}
      body.leshy-theme-forest #pane-chat .leshy-forest-ground .fern{position:absolute;bottom:26px;width:72px;height:58px;transform-origin:50% 100%;filter:drop-shadow(0 5px 6px rgba(0,0,0,.25));opacity:.68}
      body.leshy-theme-forest #pane-chat .leshy-forest-ground .fern::before{content:"";position:absolute;left:35px;bottom:0;width:2px;height:58px;background:linear-gradient(#718f59,#344c31);border-radius:99px;transform:rotate(var(--r,0deg));transform-origin:bottom}
      body.leshy-theme-forest #pane-chat .leshy-forest-ground .fern::after{content:"";position:absolute;left:9px;right:9px;bottom:14px;height:42px;background:repeating-linear-gradient(153deg,transparent 0 6px,rgba(91,124,70,.82) 7px 10px,transparent 11px 17px);clip-path:polygon(50% 0,100% 100%,51% 73%,0 100%);transform:rotate(var(--r,0deg));transform-origin:bottom}
      body.leshy-theme-forest #pane-chat .leshy-forest-ground .f1{left:1%;--r:-13deg}.f2{left:8%;--r:8deg}.f3{right:9%;--r:-8deg}.f4{right:1%;--r:14deg}

      body.leshy-theme-forest #messages .msg{overflow:visible!important;transition:border-color .25s,box-shadow .25s,transform .25s}
      body.leshy-theme-forest #messages .msg.user,
      body.leshy-theme-forest #messages .msg.assistant{
        border-color:rgba(155,197,99,.27)!important;
        box-shadow:0 15px 42px rgba(0,0,0,.24),inset 0 0 34px rgba(33,66,38,.12),0 0 0 1px rgba(83,122,62,.04)!important
      }
      body.leshy-theme-forest #messages .msg::before{height:13px!important;top:-7px!important;left:5px!important;right:5px!important;background:
        radial-gradient(ellipse at 3% 65%,#506941 0 15%,transparent 17%),
        radial-gradient(ellipse at 11% 47%,#81935d 0 13%,transparent 15%),
        radial-gradient(ellipse at 20% 62%,#49643e 0 15%,transparent 17%),
        radial-gradient(ellipse at 31% 43%,#718b55 0 13%,transparent 15%),
        radial-gradient(ellipse at 43% 61%,#3f5d39 0 15%,transparent 17%),
        radial-gradient(ellipse at 55% 44%,#81945d 0 13%,transparent 15%),
        radial-gradient(ellipse at 67% 63%,#48643d 0 15%,transparent 17%),
        radial-gradient(ellipse at 79% 45%,#718a53 0 13%,transparent 15%),
        radial-gradient(ellipse at 90% 62%,#426039 0 15%,transparent 17%),
        radial-gradient(ellipse at 98% 49%,#7d915a 0 13%,transparent 15%)!important;filter:drop-shadow(0 2px 1px rgba(0,0,0,.2)) blur(.15px)!important}
      body.leshy-theme-forest #messages .msg::after{content:"";position:absolute;pointer-events:none;left:10px;right:8px;bottom:-7px;height:14px;opacity:var(--forest-growth,.58);background:
        linear-gradient(176deg,transparent 0 39%,rgba(102,77,48,.82) 41% 48%,transparent 50%),
        radial-gradient(ellipse at 9% 42%,#4b673e 0 13%,transparent 15%),
        radial-gradient(ellipse at 25% 62%,#738a55 0 12%,transparent 14%),
        radial-gradient(ellipse at 47% 43%,#3f5c38 0 14%,transparent 16%),
        radial-gradient(ellipse at 70% 63%,#677f4e 0 12%,transparent 14%),
        radial-gradient(ellipse at 91% 46%,#45613b 0 13%,transparent 15%);filter:blur(.1px)}
      body.leshy-theme-forest #messages .msg[data-forest-age="old"]{--forest-growth:.92}
      body.leshy-theme-forest #messages .msg[data-forest-age="middle"]{--forest-growth:.72}
      body.leshy-theme-forest #messages .msg[data-forest-age="new"]{--forest-growth:.48}
      body.leshy-theme-forest #messages .msg[data-forest-age="old"]::after{height:18px;bottom:-10px}
      body.leshy-theme-forest #messages .msg[data-forest-age="old"] .who{color:#c5dd94!important;text-shadow:0 0 12px rgba(164,210,91,.18)}

      body.leshy-theme-forest .chat-compose>.composer{overflow:visible!important;border-color:rgba(166,211,100,.39)!important;box-shadow:0 13px 40px rgba(0,0,0,.34),0 0 22px rgba(117,164,66,.07),inset 0 0 34px rgba(45,75,42,.12)!important}
      body.leshy-theme-forest .chat-compose>.composer::before{height:15px!important;top:-8px!important;left:4px!important;right:4px!important;background:
        linear-gradient(176deg,transparent 0 41%,rgba(104,76,45,.74) 43% 49%,transparent 51%),
        radial-gradient(ellipse at 5% 58%,#526d43 0 14%,transparent 16%),
        radial-gradient(ellipse at 17% 41%,#80945e 0 12%,transparent 14%),
        radial-gradient(ellipse at 31% 61%,#405e38 0 14%,transparent 16%),
        radial-gradient(ellipse at 46% 42%,#738c55 0 12%,transparent 14%),
        radial-gradient(ellipse at 62% 62%,#45613a 0 14%,transparent 16%),
        radial-gradient(ellipse at 77% 42%,#7b9159 0 12%,transparent 14%),
        radial-gradient(ellipse at 91% 59%,#405e39 0 14%,transparent 16%)!important}
      body.leshy-theme-forest .chat-compose>.composer::after{content:"";position:absolute;z-index:-1;pointer-events:none;left:-17px;right:-17px;bottom:-13px;height:25px;border-bottom:3px solid rgba(112,82,49,.62);border-radius:0 0 48% 45%;box-shadow:0 5px 0 -3px rgba(90,121,64,.55),0 8px 13px rgba(0,0,0,.26)}
      body.leshy-theme-forest #chatAttachButton:hover,body.leshy-theme-forest #chatMicButton:hover{background:rgba(126,169,84,.12)!important}
      body.leshy-theme-forest #chatSend{box-shadow:0 0 0 1px rgba(185,225,123,.16),0 0 22px rgba(169,218,91,.20)!important}

      body.leshy-theme-forest #leshyFireflies i:nth-child(3n){width:5px;height:5px;box-shadow:0 0 10px #edf77f,0 0 22px rgba(226,240,102,.72),0 0 38px rgba(203,229,82,.26)}
      body.leshy-theme-forest #leshyFireflies i:nth-child(4n){opacity:.45;filter:blur(.35px)}
      body.leshy-theme-forest #leshyForestMist{opacity:.9}

      @media(max-width:820px){body.leshy-theme-forest #pane-chat .leshy-forest-ground{height:72px;opacity:.62}body.leshy-theme-forest #pane-chat .leshy-forest-ground .fern{transform:scale(.72);bottom:14px}}
      @media(prefers-reduced-motion:reduce){body.leshy-theme-forest #pane-chat .leshy-forest-v2-depth::before{transition:none!important}.leshy-forest-ground .fern{animation:none!important}}
    `;
    document.head.appendChild(style);
  }

  function installDecor(){
    const pane=$('pane-chat');
    if(!pane)return false;
    if(!pane.querySelector('.leshy-forest-v2-depth')){
      const depth=document.createElement('div');
      depth.className='leshy-forest-v2-depth';
      depth.setAttribute('aria-hidden','true');
      pane.prepend(depth);
    }
    if(!pane.querySelector('.leshy-forest-ground')){
      const ground=document.createElement('div');
      ground.className='leshy-forest-ground';
      ground.setAttribute('aria-hidden','true');
      ground.innerHTML='<i class="fern f1"></i><i class="fern f2"></i><i class="fern f3"></i><i class="fern f4"></i>';
      pane.appendChild(ground);
    }
    return true;
  }

  function ageMessages(){
    const items=[...document.querySelectorAll('#messages .msg')];
    const last=items.length-1;
    items.forEach((node,index)=>{
      const distance=last-index;
      node.dataset.forestAge=distance>=5?'old':distance>=2?'middle':'new';
    });
  }

  function boot(){
    installStyle();
    installDecor();
    ageMessages();
    const messages=$('messages');
    if(messages)new MutationObserver(ageMessages).observe(messages,{childList:true,subtree:false});
    new MutationObserver(()=>{installDecor();ageMessages()}).observe(document.documentElement,{childList:true,subtree:true});
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
