// 標本新增頁：選好物種後，自動建議下一個可用典藏編號（使用者仍可手動修改）。
(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    if (typeof django === 'undefined' || !django.jQuery) return;
    var $ = django.jQuery;
    var $species = $('#id_species');
    var catEl = document.getElementById('id_catalog_number');
    // 編輯頁的典藏編號為唯讀（無 input），或找不到物種欄 → 不處理
    if (!$species.length || !catEl || catEl.readOnly) return;

    var lastSuggested = '';

    $species.on('change', function () {
      var speciesId = $species.val();
      if (!speciesId) return;

      // 只有欄位為空、或目前值仍是上次的自動建議（使用者未手動改過）才覆寫
      var current = (catEl.value || '').trim();
      if (current !== '' && current !== lastSuggested) return;

      var url = '/admin/collection/specimen/suggest-catalog/?species=' +
        encodeURIComponent(speciesId);
      fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data && data.catalog_number) {
            catEl.value = data.catalog_number;
            lastSuggested = data.catalog_number;
          }
        })
        .catch(function () { /* 靜默失敗；使用者仍可手動輸入或留空自動產生 */ });
    });
  });
})();
