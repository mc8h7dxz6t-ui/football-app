/**
 * Production fix: hibs-racing under /racing on hibs-bet.co.uk.
 * Rewrites hardcoded root-relative nav + /api/* fetch to proxied paths.
 */
(function () {
  var PREFIX = "/racing";
  var API_PREFIX = "/api/racing";

  function rewriteHref(href) {
    if (!href || href.charAt(0) !== "/") return href;
    if (href.indexOf(PREFIX + "/") === 0 || href === PREFIX) return href;
    if (href.indexOf("/api/racing/") === 0) return href;
    if (href.indexOf("/api/") === 0) return API_PREFIX + href.slice(4);
    if (href.indexOf("/static/") === 0) return PREFIX + href;
    // Football-only roots — leave alone
    if (
      href === "/" ||
      href.indexOf("/dashboard") === 0 ||
      href.indexOf("/tracker") === 0 ||
      href.indexOf("/harvested-execution") === 0 ||
      href.indexOf("/settings") === 0
    ) {
      return href;
    }
    return PREFIX + href;
  }

  function rewriteStaticAttr(el, attr) {
    var value = el.getAttribute(attr);
    if (!value || value.charAt(0) !== "/") return;
    var next = rewriteHref(value);
    if (next && next !== value) el.setAttribute(attr, next);
  }

  document.querySelectorAll("a[href^='/']").forEach(function (a) {
    var next = rewriteHref(a.getAttribute("href"));
    if (next && next !== a.getAttribute("href")) a.setAttribute("href", next);
  });

  document.querySelectorAll("script[src^='/static/'], link[href^='/static/']").forEach(function (el) {
    rewriteStaticAttr(el, el.hasAttribute("src") ? "src" : "href");
  });

  document.querySelectorAll("[data-api-url^='/api/']").forEach(function (el) {
    var u = el.getAttribute("data-api-url");
    if (u && u.indexOf("/api/racing/") !== 0) {
      el.setAttribute("data-api-url", API_PREFIX + u.slice(4));
    }
  });

  if (typeof window.fetch === "function" && !window.__hibsRacingFetchPatched) {
    window.__hibsRacingFetchPatched = true;
    var orig = window.fetch.bind(window);
    window.fetch = function (input, init) {
      if (typeof input === "string" && input.indexOf("/api/") === 0 && input.indexOf("/api/racing/") !== 0) {
        input = API_PREFIX + input.slice(4);
      }
      return orig(input, init);
    };
  }
})();
