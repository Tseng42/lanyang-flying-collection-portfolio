// 標本新增頁：選好「類群」（或物種）後，自動建議下一個可用典藏編號（仍可手動修改）。
// 類群代碼依 taxon_group 產生，故以類群為主；未選類群但選了物種時，後端會沿用物種類群。
(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    if (typeof django === 'undefined' || !django.jQuery) return;
    var $ = django.jQuery;
    var $taxonGroup = $('#id_taxon_group');
    var $species = $('#id_species');
    var $accYear = $('#id_accession_year');
    var catEl = document.getElementById('id_catalog_number');
    // 編輯頁的典藏編號為唯讀（無 input），或找不到相關欄位 → 不處理
    if (!catEl || catEl.readOnly) return;
    if (!$taxonGroup.length && !$species.length) return;

    var lastSuggested = '';

    function suggest() {
      var taxonGroup = $taxonGroup.length ? ($taxonGroup.val() || '') : '';
      var speciesId = $species.length ? ($species.val() || '') : '';
      if (!taxonGroup && !speciesId) return;

      // 只有欄位為空、或目前值仍是上次的自動建議（使用者未手動改過）才覆寫
      var current = (catEl.value || '').trim();
      if (current !== '' && current !== lastSuggested) return;

      var params = taxonGroup
        ? 'taxon_group=' + encodeURIComponent(taxonGroup)
        : 'species=' + encodeURIComponent(speciesId);
      // 帶入表單上的入藏年份；僅在為數字時傳出，空或非數字則不帶，
      // 交由後端 suggest_catalog_view 維持「以今年估算」的既有行為。
      var yearVal = $accYear.length ? ($accYear.val() || '').trim() : '';
      if (/^\d+$/.test(yearVal)) {
        params += '&year=' + encodeURIComponent(yearVal);
      }
      var url = '/admin/collection/specimen/suggest-catalog/?' + params;
      fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data && data.catalog_number) {
            catEl.value = data.catalog_number;
            lastSuggested = data.catalog_number;
          }
        })
        .catch(function () { /* 靜默失敗；使用者仍可手動輸入或留空自動產生 */ });
    }

    if ($taxonGroup.length) $taxonGroup.on('change', suggest);
    if ($species.length) $species.on('change', suggest);
    // 入藏年份改變時重新請求預覽（change 於失焦、input 於逐字輸入皆觸發）
    if ($accYear.length) $accYear.on('change input', suggest);
  });
})();
