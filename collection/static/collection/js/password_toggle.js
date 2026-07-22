/* 登入頁密碼欄位的顯示／隱藏切換（純 JavaScript，無框架、無新圖示套件）。
   使用 unfold 既有的 material-symbols 圖示字型；切換鈕為 <button>，
   可用鍵盤操作（Tab 聚焦、Enter/Space 觸發），並帶有 aria-label。
   圖示以絕對定位置於輸入框「內部」右側並垂直置中，不遮擋輸入文字。 */
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
    var input = document.getElementById("id_password");
    if (!input || input.dataset.toggleBound) {
      return;
    }
    input.dataset.toggleBound = "1";

    // 只把 input 包進一個貼齊輸入框大小的相對定位容器，
    // 避免容器把上方的 label 也算進去，導致圖示浮到右上角。
    var wrap = document.createElement("span");
    wrap.style.cssText = "position:relative; display:block; width:100%;";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    // 輸入框右側預留空間，避免文字被圖示蓋住。
    input.style.paddingRight = "2.5rem";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "material-symbols-outlined";  // unfold 既有圖示字型
    btn.textContent = "visibility";
    btn.setAttribute("aria-label", "顯示密碼");
    btn.style.cssText =
      "position:absolute; top:0; bottom:0; right:0.5rem; margin:auto 0;" +
      "display:flex; align-items:center; justify-content:center;" +
      "height:100%; width:1.5rem; background:none; border:0; padding:0;" +
      "cursor:pointer; color:inherit; opacity:0.7; font-size:1.25rem;" +
      "line-height:1;";
    btn.addEventListener("mouseenter", function () { btn.style.opacity = "1"; });
    btn.addEventListener("mouseleave", function () { btn.style.opacity = "0.7"; });
    btn.addEventListener("focus", function () { btn.style.opacity = "1"; });
    btn.addEventListener("blur", function () { btn.style.opacity = "0.7"; });

    function update(show) {
      input.type = show ? "text" : "password";
      btn.textContent = show ? "visibility_off" : "visibility";
      btn.setAttribute("aria-label", show ? "隱藏密碼" : "顯示密碼");
    }
    btn.addEventListener("click", function () {
      update(input.type === "password");
    });

    wrap.appendChild(btn);
  });
})();
