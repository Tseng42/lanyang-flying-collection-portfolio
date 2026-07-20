/* 標本表單「學名」欄位：自由輸入 + 即時建議既有物種（學名／中文名皆比對）。
   不強制選取；找不到既有物種時，可直接以輸入文字送出（後端會自動建立物種）。 */
(function () {
  "use strict";
  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    var input = document.querySelector('input[data-species-autocomplete]');
    if (!input) return;

    // 建議清單容器
    var box = document.createElement("div");
    box.className = "species-suggest-box";
    box.style.cssText =
      "position:absolute;z-index:1000;background:#fff;border:1px solid #ccc;" +
      "border-radius:6px;max-height:240px;overflow:auto;min-width:260px;" +
      "box-shadow:0 6px 18px -8px rgba(0,0,0,.35);display:none;font-size:14px;";
    document.body.appendChild(box);

    var timer = null;
    var items = [];
    var active = -1;

    function positionBox() {
      var r = input.getBoundingClientRect();
      box.style.left = (window.scrollX + r.left) + "px";
      box.style.top = (window.scrollY + r.bottom + 2) + "px";
      box.style.width = r.width + "px";
    }

    function hide() { box.style.display = "none"; active = -1; }

    function choose(name) { input.value = name; hide(); input.focus(); }

    function render(results) {
      box.innerHTML = "";
      items = results;
      if (!results.length) { hide(); return; }
      results.forEach(function (r, i) {
        var row = document.createElement("div");
        row.className = "species-suggest-item";
        row.style.cssText = "padding:7px 12px;cursor:pointer;";
        var common = r.common_name ? "（" + r.common_name + "）" : "";
        row.textContent = r.scientific_name + common;
        row.addEventListener("mousedown", function (e) {
          e.preventDefault();            // 避免 blur 先觸發
          choose(r.scientific_name);
        });
        row.addEventListener("mouseenter", function () { setActive(i); });
        box.appendChild(row);
      });
      positionBox();
      box.style.display = "block";
    }

    function setActive(i) {
      var rows = box.children;
      for (var k = 0; k < rows.length; k++) {
        rows[k].style.background = (k === i) ? "#eef2f7" : "#fff";
      }
      active = i;
    }

    function query() {
      var q = input.value.trim();
      if (q.length < 1) { hide(); return; }
      var url = "/admin/collection/specimen/species-search/?q=" +
        encodeURIComponent(q);
      fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.ok ? r.json() : { results: [] }; })
        .then(function (data) { render((data && data.results) || []); })
        .catch(function () { hide(); });
    }

    input.setAttribute("autocomplete", "off");
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(query, 200);
    });
    input.addEventListener("keydown", function (e) {
      if (box.style.display === "none") return;
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(active + 1, items.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(active - 1, 0)); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); choose(items[active].scientific_name); }
      else if (e.key === "Escape") { hide(); }
    });
    input.addEventListener("blur", function () { setTimeout(hide, 150); });
    window.addEventListener("resize", positionBox);
  });
})();
