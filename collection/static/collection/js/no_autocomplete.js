// 關閉後台所有文字輸入欄的瀏覽器自動完成/歷史紀錄提示。
// 不處理密碼欄，保留瀏覽器/密碼管理員的預設安全行為。
(function () {
  function disableAutocomplete(root) {
    var selector = [
      'input[type=text]',
      'input[type=search]',
      'input[type=email]',
      'input[type=url]',
      'input[type=tel]',
      'input[type=number]',
      'input[type=date]',
      'input:not([type])',
      'textarea',
    ].join(', ');
    root.querySelectorAll(selector).forEach(function (el) {
      el.setAttribute('autocomplete', 'off');
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    disableAutocomplete(document);
    // 後台 inline「新增一列」動態插入的欄位也一併處理
    document.addEventListener('formset:added', function (event) {
      if (event.target) disableAutocomplete(event.target);
    });
  });
})();
