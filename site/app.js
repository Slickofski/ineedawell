/* I Need a Well — intake form -> Supabase submit_well_request RPC.
   The orders table refuses direct anon inserts by design (RLS);
   this RPC is the only public door. It validates the request,
   enforces the pilot cap / intake-open switch server-side, and
   returns the server-issued order_ref ("ORD-<uuid>").
   Anon key is public by design; it ships in client-side code. */
(function () {
  "use strict";

  // ============================================================
  // CONFIG — point this at your own backend.
  //
  // The intake form submits to a Supabase project through its
  // submit_well_request RPC (see the header comment above). Set
  // SUPABASE_URL and SUPABASE_ANON_KEY to YOUR project's values
  // (Project Settings -> API in your Supabase dashboard; the anon
  // key is public by design and safe to ship in client code).
  //
  // Leave the placeholders as-is to see the demo form: the page
  // and form render normally, but nothing is ever submitted —
  // and it can never silently submit to someone else's project.
  // ============================================================
  var SUPABASE_URL = "https://YOUR-PROJECT-REF.supabase.co";
  var SUPABASE_ANON_KEY = "YOUR-SUPABASE-ANON-KEY";

  function makeRequestId() {
    // One request id per page load: the RPC treats a repeat call
    // with the same id + same email/address as an idempotent
    // retry and returns the same order_ref, so a double submit or
    // a retried request can never create a duplicate order.
    var cryptoObj = (typeof window !== "undefined" && window.crypto) ||
      (typeof globalThis !== "undefined" && globalThis.crypto);
    if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
      return cryptoObj.randomUUID();
    }
    // RFC 4122 v4 fallback for older browsers.
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (ch) {
      var r = (Math.random() * 16) | 0;
      var v = ch === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
  var REQUEST_ID = makeRequestId();

  function $(id) { return document.getElementById(id); }

  // Reveal/hide that works under BOTH page stylesheets: the goal
  // page reveals via the .show class (styles.css .form-state), the
  // repo page carries the hidden attribute and pilot.css pins
  // [hidden]{display:none!important} — a class can never beat
  // !important, so hidden must be cleared/set as well.
  function showState(el) {
    if (!el) return;
    el.hidden = false;
    if (el.classList && typeof el.classList.add === "function") {
      el.classList.add("show");
    }
  }
  function hideState(el) {
    if (!el) return;
    el.hidden = true;
    if (el.classList && typeof el.classList.remove === "function") {
      el.classList.remove("show");
    }
  }

  // Failure panels (CX pass, 2026-10-29; audit Finding 6): the
  // RPC's refusal reasons are distinct outcomes and get distinct,
  // honest panels — a closed intake is not "something went wrong
  // on our end." The match strings are the function's actual RAISE
  // texts (migration 001, submit_well_request): the closed and
  // invalid texts were live-verified against the deployed function
  // on 2026-10-29 with pre-insert probes (orders count unchanged);
  // the limit/conflict texts are from the same applied migration.
  // Anything unrecognized — 5xx, a network failure, the 15 s
  // abort — lands on the technical panel.
  var PANEL_BY_MESSAGE = [
    ["Pilot intake is closed", "state-closed"],
    ["Pilot intake limit reached", "state-full"],
    ["Invalid intake", "state-invalid"],
    ["Request ID conflict", "state-invalid"]
  ];
  var PANEL_IDS = ["state-ok", "state-fail", "state-closed",
                   "state-full", "state-invalid"];

  function panelForMessage(message) {
    if (message) {
      for (var i = 0; i < PANEL_BY_MESSAGE.length; i++) {
        if (String(message).indexOf(PANEL_BY_MESSAGE[i][0]) !== -1) {
          return PANEL_BY_MESSAGE[i][1];
        }
      }
    }
    return "state-fail";
  }

  function showOnlyPanel(id) {
    PANEL_IDS.forEach(function (pid) {
      if (pid === id) showState($(pid)); else hideState($(pid));
    });
  }

  // Repo-variant tabs (Finding 5): a tablist whose buttons name
  // their panels via aria-controls. No-op where tabs are absent
  // (goal page, test stubs) — feature-checked throughout.
  function initTabs() {
    if (typeof document.querySelectorAll !== "function") return;
    var tabs = document.querySelectorAll('[role="tab"]');
    if (!tabs || !tabs.length) return;
    tabs = Array.prototype.slice.call(tabs);

    function activate(tab) {
      tabs.forEach(function (t) {
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        if (panel) panel.hidden = t !== tab;
      });
    }

    tabs.forEach(function (t) {
      if (typeof t.addEventListener === "function") {
        t.addEventListener("click", function () { activate(t); });
      }
    });

    function activateFromHash() {
      if (typeof location === "undefined" || !location.hash) return;
      var id = String(location.hash).replace(/^#/, "");
      if (!id) return;
      for (var i = 0; i < tabs.length; i++) {
        if (tabs[i].getAttribute("aria-controls") === id) {
          activate(tabs[i]);
          return;
        }
      }
      // Any other hash (#towns, #request, ...) is not a tab switch:
      // leave the current selection alone.
    }

    activateFromHash();
    if (typeof window !== "undefined" && window &&
        typeof window.addEventListener === "function") {
      window.addEventListener("hashchange", activateFromHash);
    }
  }

  function setInvalid(fieldName, invalid) {
    var el = document.querySelector('.field[data-field="' + fieldName + '"]');
    if (el) el.classList.toggle("invalid", !!invalid);
  }

  function validEmail(v) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTabs();
    var form = $("intake-form");
    if (!form) return;
    var btn = $("submit-btn");
    var originalBtnLabel = btn ? btn.textContent : "";

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();

      var email = $("f-email").value.trim();
      var address = $("f-address").value.trim();

      var ok = true;
      setInvalid("email", !validEmail(email)); if (!validEmail(email)) ok = false;
      setInvalid("address", !address);      if (!address) ok = false;
      if (!ok) return;

      btn.disabled = true;
      btn.textContent = "Sending…";

      var body = {
        p_request_id: REQUEST_ID,
        p_email: email,
        p_address: address
      };

      // Bounded wait (restored in fix pass 2, Finding A): the
      // pre-rewire form aborted a stalled request after 15 s; the
      // rewire dropped the AbortController + timer, so a hung
      // connection wedged the button at "Sending…" forever and the
      // failure handler never ran. Aborting rejects the fetch,
      // which lands in the same .catch as every other failure —
      // form stays up, button restored, same REQUEST_ID on retry.
      var controller = typeof AbortController !== "undefined"
        ? new AbortController() : null;
      var timer = controller
        ? setTimeout(function () { controller.abort(); }, 15000) : null;

      fetch(SUPABASE_URL + "/rest/v1/rpc/submit_well_request", {
        method: "POST",
        headers: {
          "apikey": SUPABASE_ANON_KEY,
          "Authorization": "Bearer " + SUPABASE_ANON_KEY,
          "Content-Type": "application/json"
        },
        body: JSON.stringify(body),
        signal: controller ? controller.signal : undefined
      }).then(function (res) {
        if (res.status !== 200) {
          // Read the refusal body before throwing: PostgREST
          // returns the RPC's raise text as JSON .message, and
          // the panel mapping keys off it (see PANEL_BY_MESSAGE).
          return res.json().catch(function () { return null; })
            .then(function (body) {
              var err = new Error("HTTP " + res.status);
              err.panelId = panelForMessage(body && body.message);
              throw err;
            });
        }
        return res.json();
      }).then(function (orderRef) {
        form.style.display = "none";
        $("order-ref").textContent = orderRef;
        showOnlyPanel("state-ok");
      }).catch(function (err) {
        // Finding 4: do NOT hide the form here. Leave it usable so
        // the user can retry in-page — REQUEST_ID is a per-page-load
        // const, so the retry reuses the same p_request_id and the
        // RPC treats it as the same (idempotent) request. Restore
        // the button from its "Sending…" state.
        var panelId = (err && err.panelId) || "state-fail";
        // Closed/full are terminal for this batch: the form stays
        // on screen with the panel's email fallback (CX workstream
        // decision, 2026-10-29) but the button stays disabled —
        // there is nothing to retry. Every other outcome restores
        // the button for an in-page retry.
        var terminal = panelId === "state-closed" || panelId === "state-full";
        if (btn) {
          btn.disabled = terminal;
          btn.textContent = originalBtnLabel;
        }
        showOnlyPanel(panelId);
      }).finally(function () {
        if (timer !== null) clearTimeout(timer);
      });
    });
  });
})();
