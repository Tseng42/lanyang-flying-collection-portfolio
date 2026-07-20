/* 物種編輯頁：選到「非一般類」保育等級（含待查證）時，顯示醒目提示。
   純前端提示，不影響儲存邏輯（後端仍以 conservation_status 判斷）。 */
(function () {
  "use strict";
  function ready(fn) {
    if (document.readyState !== "loading") {
      fn();
    } else {
      document.addEventListener("DOMContentLoaded", fn);
    }
  }
  ready(function () {
    var sel = document.getElementById("id_conservation_status");
    if (!sel) {
      return;
    }
    var banner = document.createElement("div");
    banner.id = "conservation-hint-banner";
    banner.style.cssText =
      "margin:8px 0;padding:10px 14px;border-radius:8px;" +
      "background:#fbeae8;border:1px solid #b3261e;color:#7a2019;" +
      "font-weight:600;line-height:1.6;display:none;";
    banner.textContent =
      "⚠ 本物種標為保育類（或待查證）：公開頁將不顯示其觀察影像，" +
      "且標本地點僅公開至縣市層級。若確屬一般類，請改選「一般類」。";
    var row = sel.closest(".form-row, .field-conservation_status, div");
    if (row && row.parentNode) {
      row.parentNode.insertBefore(banner, row.nextSibling);
    } else {
      sel.parentNode.appendChild(banner);
    }
    function update() {
      var v = sel.value;
      banner.style.display = v && v !== "general" ? "block" : "none";
    }
    sel.addEventListener("change", update);
    update();
  });
})();
