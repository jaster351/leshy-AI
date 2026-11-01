(function(){
  'use strict';
  // SSE разбирается один раз в chat-multitask.js. Этот мост только синхронизирует
  // визуальный статус этапов: как только пошёл текст ответа, панель поиска больше
  // не должна выглядеть как зависшая операция, в том числе в новом ещё безымянном чате.
  window.__leshyActivityFetchBridge=true;
  function hideLiveActivity(){
    document.querySelectorAll('.leshy-live-activity').forEach(node=>{node.style.display='none'});
  }
  window.addEventListener('leshy:delta',hideLiveActivity);
  window.addEventListener('leshy:done',hideLiveActivity);
})();
