(() => {
  const root = document.getElementById("app");
  const toast = document.getElementById("toast");
  const invitationQuery = new URLSearchParams(window.location.search);
  const invitationFragment = new URLSearchParams(window.location.hash.slice(1));
  const invitationToken = invitationFragment.get("token") || invitationQuery.get("token") || "";
  if (invitationToken) window.history.replaceState({}, document.title, window.location.pathname);
  const state = { user: null, organizations: [], organization: null, properties: [], units: [], tickets: [], tenancies: [], activeTenancyEndId: null, activePaymentTenancyId: null, paymentRecords: [], rentObligations: [], paymentError: "", rentQaAnswer: null, rentQaError: "", activeMpesaAttempt: null, mpesaSettings: null, mpesaSettingsError: "", members: [], teamError: "", teamAuthorized: false, invitationToken, invitation: null, invitationError: "", invitationResult: "", inviteUnitId: null, invitationManageUnitId: null, unitInvitations: [], invitationListError: "", property: null, activeTicket: null, activeListingUnitId: null, activeListingData: null, publicListings: [], publicFilters: {}, publicError: "", publicLoading: false, page: "overview", online: navigator.onLine };

  const escapeHtml = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const initials = (name) => (name || "N").split(/[\s@._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join("");
  const readCookie = (name) => document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${name}=`))?.slice(name.length + 1) || "";

  async function api(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (!new Set(["GET", "HEAD", "OPTIONS", "TRACE"]).has(method)) headers.set("X-CSRFToken", readCookie("csrftoken"));
    const response = await fetch(path, { ...options, method, headers, credentials: "same-origin" });
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) { state.user = null; render(); }
      const detail = body.detail || Object.entries(body).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(" ") : value}`).join(" · ");
      throw new Error(detail || `Request failed (${response.status}).`);
    }
    return body;
  }

  function notify(message) {
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(notify.timeout);
    notify.timeout = window.setTimeout(() => toast.classList.remove("show"), 3000);
  }

  async function loadWorkspace() {
    state.organizations = await api("/api/v1/organizations");
    if (!state.organizations.length) { state.organization = null; state.properties = []; state.units = []; state.tickets = []; state.tenancies = []; render(); return; }
    const wantedId = state.organization?.id;
    state.organization = state.organizations.find((item) => item.id === wantedId) || state.organizations[0];
    state.properties = await api(`/api/v1/organizations/${state.organization.id}/properties`);
    state.property = state.properties.find((item) => item.id === state.property?.id) || state.properties[0] || null;
    const unitGroups = await Promise.all(state.properties.map(async (property) => {
      try { return await api(`/api/v1/properties/${property.id}/units`); } catch { return []; }
    }));
    state.units = unitGroups.flat();
    try { state.tickets = await api("/api/v1/maintenance-tickets"); } catch { state.tickets = []; }
    try {
      const allTenancies = await api("/api/v1/tenancies");
      const visiblePropertyIds = new Set(state.properties.map((property) => property.id));
      state.tenancies = allTenancies.filter((tenancy) => visiblePropertyIds.has(tenancy.property_id));
    } catch { state.tenancies = []; }
    render();
  }

  async function boot() {
    if (window.location.pathname.replace(/\/$/, "") === "/listings") {
      await loadPublicListings();
      return;
    }
    try {
      state.user = await api("/api/v1/auth/me");
      if (state.invitationToken) await loadInvitation();
      else await loadWorkspace();
    } catch {
      state.user = null;
      render();
    }
  }

  async function loadPublicListings(filters = state.publicFilters) {
    state.publicFilters = filters;
    state.publicError = "";
    state.publicLoading = true;
    render();
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== "" && value != null));
    const queryString = query.toString();
    try { state.publicListings = await api(`/api/v1/public/listings${queryString ? `?${queryString}` : ""}`); }
    catch (error) { state.publicListings = []; state.publicError = error.message; }
    state.publicLoading = false;
    render();
  }

  function publicListingsView() {
    const cards = state.publicListings.map((listing) => {
      const amount = new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 }).format(Number(listing.monthly_rent));
      const features = [listing.unit_type, listing.bedrooms == null ? "" : `${listing.bedrooms} bed`, listing.bathrooms == null ? "" : `${listing.bathrooms} bath`].filter(Boolean).join(" · ");
      const place = [listing.location_label, listing.county].filter(Boolean).join(" · ") || "Kenya";
      const phone = String(listing.contact_phone || "").replace(/[^\d+]/g, "");
      const phoneDigits = phone.replace(/\D/g, "");
      const whatsappNumber = phoneDigits.startsWith("0") ? `254${phoneDigits.slice(1)}` : phoneDigits;
      const contact = phone ? `<a class="secondary-btn" href="tel:${escapeHtml(phone)}">Call landlord</a><a class="primary-btn" href="https://wa.me/${escapeHtml(whatsappNumber)}" target="_blank" rel="noopener noreferrer">WhatsApp</a>` : `<span class="subtle">Contact details not provided</span>`;
      const cover = listing.photos?.[0];
      const artwork = cover ? `<img src="${escapeHtml(cover.image)}" alt="${escapeHtml(cover.alt_text || listing.title)}" loading="lazy" decoding="async">` : "⌂";
      return `<article class="public-listing-card"><div class="public-listing-art" ${cover ? "" : 'aria-hidden="true"'}>${artwork}</div><div class="public-listing-copy"><div class="public-listing-place">${escapeHtml(place)}</div><h2>${escapeHtml(listing.title)}</h2><div class="public-listing-features">${escapeHtml(features || "Rental home")} · ${escapeHtml(listing.organization_name)}</div><p>${escapeHtml(listing.description || "Contact the landlord for more information.")}</p><div class="public-listing-foot"><strong>${escapeHtml(listing.currency)} ${escapeHtml(amount)} <span>/ month</span></strong><div class="public-listing-contact">${contact}</div></div></div></article>`;
    }).join("");
    return `<main id="main" class="public-rentals"><header class="public-rentals-header"><a class="brand-lockup" href="/"><span class="brand-mark">H</span><span>HomeConnect</span></a><a class="secondary-btn" href="/">Landlord sign in</a></header><section class="public-rentals-intro"><span class="page-kicker">Find your next home</span><h1>Homes to rent in Kenya</h1><p>Browse homes shared by HomeConnect landlords.</p></section><form id="rental-search-form" class="public-search panel"><div class="field"><label for="rental-query">Area or keywords</label><input id="rental-query" name="q" value="${escapeHtml(state.publicFilters.q || "")}" placeholder="e.g. Kilimani"></div><div class="field"><label for="rental-county">County</label><input id="rental-county" name="county" value="${escapeHtml(state.publicFilters.county || "")}" placeholder="e.g. Nairobi"></div><div class="field"><label for="rental-home-type">Home type</label><select id="rental-home-type" name="home_type"><option value="">Any type</option>${[["bedsitter", "Bedsitter"], ["single room", "Single room"], ["1 bedroom", "1 bedroom"], ["2 bedroom", "2 bedroom"], ["3 bedroom", "3 bedroom"], ["4 bedroom", "4+ bedroom"]].map(([value, label]) => `<option value="${value}" ${state.publicFilters.home_type === value ? "selected" : ""}>${label}</option>`).join("")}</select></div><div class="field"><label for="rental-bedrooms">Bedrooms</label><select id="rental-bedrooms" name="bedrooms"><option value="">Any</option>${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${String(state.publicFilters.bedrooms || "") === String(n) ? "selected" : ""}>${n}${n === 5 ? "+" : ""}</option>`).join("")}</select></div><div class="field"><label for="rental-min-rent">Min monthly rent</label><input id="rental-min-rent" name="min_rent" type="number" min="0" step="100" value="${escapeHtml(state.publicFilters.min_rent || "")}" placeholder="KES"></div><div class="field"><label for="rental-max-rent">Max monthly rent</label><input id="rental-max-rent" name="max_rent" type="number" min="0" step="100" value="${escapeHtml(state.publicFilters.max_rent || "")}" placeholder="KES"></div><button class="primary-btn" type="submit">Search homes</button></form><section class="public-rentals-results"><div class="section-toolbar"><h2>Available homes <span class="subtle">${state.publicListings.length}</span></h2></div>${state.publicError ? `<p class="form-error visible" role="alert">${escapeHtml(state.publicError)}</p>` : state.publicLoading ? `<p class="loading-inline">Searching available homes…</p>` : cards || `<div class="panel empty-state"><div class="empty-icon">⌂</div><h3>No homes match those filters</h3><p>Try a different area or rent range.</p></div>`}</section><footer class="public-rentals-footer">HomeConnect · Kenya</footer></main>`;
  }

  function statusPill(value) {
    return `<span class="status">${escapeHtml(String(value || "unknown").replaceAll("_", " "))}</span>`;
  }

  function loginView() {
    return `<main id="main" class="login-wrap">
      <section class="login-story" aria-label="About HomeConnect">
        <div class="brand-lockup"><span class="brand-mark">H</span><span>HomeConnect</span></div>
        <div class="story-copy"><span class="eyebrow">Your property, in good order</span><h1>Less chasing.<br>More clarity.</h1><p>A calm place to manage homes, keep tenants informed, and stay on top of the details that matter.</p></div>
        <div class="story-footer">Built for property teams in Kenya · KES · Africa/Nairobi</div>
      </section>
      <section class="login-panel"><form id="login-form" class="login-card">
        <span class="page-kicker">Welcome back</span><h2>Sign in to HomeConnect</h2><p>${state.invitationToken ? "Sign in with the email address that received the tenancy invitation to review its terms." : "Use the email address and password on your account."}</p>
        <div id="login-error" class="form-error" role="alert"></div>
        <div class="field"><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="username" required placeholder="you@example.com"></div>
        <div class="field"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required></div>
        <button class="primary-btn" type="submit">Sign in <span aria-hidden="true">→</span></button>
        <p class="subtle public-link"><a href="/listings">Browse available rentals</a></p>
      </form></section>
    </main>`;
  }

  function orgSetupView() {
    return `<main id="main" class="main-area"><section class="organization-empty"><span class="brand-mark">N</span><h1>Set up your workspace</h1><p>Create your organization to start adding properties and working with your team.</p><div id="workspace-error" class="form-error" role="alert"></div><form id="organization-form"><div class="field"><label for="organization-name">Organization name</label><input id="organization-name" name="name" required maxlength="180" placeholder="e.g. Acacia Homes"></div><button class="primary-btn" type="submit">Create workspace</button></form></section></main>`;
  }

  function invitationView() {
    const { property, unit, terms } = state.invitation;
    const endDate = terms.end_date ? escapeHtml(terms.end_date) + " (exclusive)" : "No end date specified";
    return `<main id="main" class="invitation-wrap"><section class="panel invitation-card"><span class="page-kicker">Tenancy invitation</span><h1>Review your proposed tenancy</h1><p class="subtle">Check the details carefully. Accept only if they match what you agreed with the property team.</p><div class="invitation-property"><strong>${escapeHtml(property.name)} · ${escapeHtml(unit.code)}</strong><span>Invitation expires ${escapeHtml(new Date(state.invitation.expires_at).toLocaleString("en-KE", { dateStyle: "medium", timeStyle: "short" }))}</span></div><dl class="terms-list"><div><dt>Start date</dt><dd>${escapeHtml(terms.start_date)}</dd></div><div><dt>End date</dt><dd>${endDate}</dd></div><div><dt>Rent</dt><dd>${escapeHtml(terms.currency)} ${escapeHtml(terms.agreed_rent)} · due day ${escapeHtml(terms.due_day)}</dd></div><div><dt>Deposit</dt><dd>${escapeHtml(terms.currency)} ${escapeHtml(terms.deposit_amount || "0.00")}</dd></div>${terms.deposit_terms ? `<div class="terms-wide"><dt>Deposit terms</dt><dd>${escapeHtml(terms.deposit_terms)}</dd></div>` : ""}${terms.agreement_reference ? `<div class="terms-wide"><dt>Agreement reference</dt><dd>${escapeHtml(terms.agreement_reference)}</dd></div>` : ""}<div><dt>Terms version</dt><dd>${escapeHtml(terms.terms_version)}</dd></div></dl><div id="invitation-error" class="form-error ${state.invitationError ? "visible" : ""}" role="alert">${escapeHtml(state.invitationError)}</div><form id="invitation-accept-form"><label class="check-option"><input type="checkbox" name="confirm_terms" required> <span>I have reviewed these terms and agree to accept this tenancy.</span></label><div class="invitation-actions"><button class="primary-btn" type="submit">Accept tenancy</button><button class="secondary-btn" type="button" data-action="decline-invitation">Decline invitation</button></div></form><p class="subtle invitation-footnote">Your acceptance is recorded with the account you signed in with. You cannot accept for another person.</p></section></main>`;
  }

  function invitationResultView() {
    const accepted = state.invitationResult === "accepted";
    return `<main id="main" class="invitation-wrap"><section class="panel invitation-card"><span class="page-kicker">${accepted ? "Invitation accepted" : "Invitation declined"}</span><h1>${accepted ? "Your tenancy is confirmed" : "The invitation was declined"}</h1><p class="subtle">${accepted ? "Your acceptance has been recorded. You can now open your HomeConnect workspace." : "The property team can send a new invitation if the terms need to change."}</p><button class="primary-btn" data-action="finish-invitation">${accepted ? "Open workspace" : "Done"}</button></section></main>`;
  }

  async function loadInvitation() {
    try {
      state.invitation = await api("/api/v1/invitations/review", { method: "POST", body: JSON.stringify({ token: state.invitationToken }) });
      state.invitationError = "";
    } catch (error) {
      state.invitation = null;
      state.invitationError = error.message;
    }
    render();
  }

  function navButton(page, icon, label) {
    return `<button class="nav-button ${state.page === page ? "active" : ""}" data-page="${page}" aria-current="${state.page === page ? "page" : "false"}"><span class="nav-icon" aria-hidden="true">${icon}</span><span>${label}</span></button>`;
  }

  function shell(content) {
    const displayName = state.user.display_name || state.user.first_name || state.user.email;
    const orgOptions = state.organizations.map((org) => `<option value="${escapeHtml(org.id)}" ${org.id === state.organization?.id ? "selected" : ""}>${escapeHtml(org.name)}</option>`).join("");
    return `<div class="app-layout">
      <aside class="sidebar"><div class="brand-lockup"><span class="brand-mark">H</span><span>HomeConnect</span></div>
        <div class="workspace-label">Workspace</div><nav class="side-nav" aria-label="Main navigation">${navButton("overview", "⌂", "Overview")}${navButton("properties", "▦", "Properties")}${navButton("maintenance", "⚒", "Maintenance")}${navButton("tenancies", "▤", "Tenancies")}${state.organization?.my_role === "owner" ? navButton("payments", "◉", "M-Pesa") : ""}${navButton("team", "♙", "Team")}</nav>
        <div class="sidebar-bottom"><label class="workspace-label" for="organization-select">Organization</label><select id="organization-select" class="org-select" aria-label="Choose organization">${orgOptions}</select>
          <div class="profile-chip"><div class="avatar">${escapeHtml(initials(displayName))}</div><div class="profile-meta"><strong>${escapeHtml(displayName)}</strong><span>${escapeHtml(state.user.email)}</span></div><button class="icon-btn" data-action="logout" aria-label="Sign out" title="Sign out">↗</button></div>
        </div>
      </aside>
      <main id="main" class="main-area"><header class="topbar"><div class="mobile-brand"><span class="brand-mark">H</span><span>HomeConnect</span></div><span class="breadcrumb">${escapeHtml(state.organization?.name || "Workspace")} <span aria-hidden="true">/</span> ${state.page[0].toUpperCase() + state.page.slice(1)}</span><div class="topbar-right"><span class="live-pill"><i class="live-dot"></i>${state.online ? "All systems ready" : "Offline · reconnect to sync"}</span></div></header>${content}</main>
    </div>`;
  }

  function overviewView() {
    const openTickets = state.tickets.filter((ticket) => !["resolved", "closed", "cancelled"].includes(ticket.status)).length;
    const availableUnits = state.units.filter((unit) => unit.status === "available").length;
    const recentProperties = state.properties.slice(0, 4).map((property) => `<div class="property-row"><div class="property-thumb" aria-hidden="true">⌂</div><div class="property-copy"><strong>${escapeHtml(property.name)}</strong><span>${escapeHtml([property.county, property.address].filter(Boolean).join(" · ") || "Location not added")}</span></div>${statusPill(property.status)}</div>`).join("");
    const recentTickets = state.tickets.slice(0, 5).map((ticket) => `<div class="ticket-item"><i class="ticket-mark"></i><div><strong>${escapeHtml(ticket.category)} · ${escapeHtml(ticket.property_name || "Property")}</strong><span>${escapeHtml(ticket.unit_code || "Unit")} · ${escapeHtml(ticket.created_at ? new Date(ticket.created_at).toLocaleDateString("en-KE", { day: "numeric", month: "short" }) : "Recently")}</span></div>${statusPill(ticket.status)}</div>`).join("");
    return `<section class="page-heading"><div><span class="page-kicker">Your portfolio</span><h1>Good to see you, ${escapeHtml((state.user.display_name || state.user.first_name || "there").split(" ")[0])}.</h1><p>Here’s what’s happening across ${escapeHtml(state.organization.name)}.</p></div><button class="primary-btn" data-page="properties">View properties <span aria-hidden="true">→</span></button></section>
      <section class="stat-grid" aria-label="Portfolio summary"><article class="stat-card"><span class="stat-icon">⌂</span><div class="stat-label">Properties</div><div class="stat-value">${state.properties.length}</div><div class="stat-foot">In this workspace</div></article><article class="stat-card"><span class="stat-icon">▦</span><div class="stat-label">Units</div><div class="stat-value">${state.units.length}</div><div class="stat-foot">${availableUnits} currently available</div></article><article class="stat-card"><span class="stat-icon">⚒</span><div class="stat-label">Open maintenance</div><div class="stat-value">${openTickets}</div><div class="stat-foot">Across visible properties</div></article><article class="stat-card"><span class="stat-icon">◷</span><div class="stat-label">Today</div><div class="stat-value">${new Date().toLocaleDateString("en-KE", { day: "numeric" })}</div><div class="stat-foot">${new Date().toLocaleDateString("en-KE", { month: "long", year: "numeric", timeZone: "Africa/Nairobi" })}</div></article></section>
      <div class="content-grid"><section class="panel"><div class="panel-head"><h2>Your properties</h2><button class="secondary-btn" data-page="properties">See all</button></div><div class="property-list">${recentProperties || `<div class="empty-state"><div class="empty-icon">⌂</div><h3>A good place to start</h3><p>Add your first property to bring its units and maintenance activity into one view.</p><button class="secondary-btn" data-page="properties">Add a property</button></div>`}</div></section>
        <div class="content-grid" style="grid-template-columns:1fr;align-content:start"><section class="panel"><div class="panel-head"><h2>Maintenance activity</h2><button class="secondary-btn" data-page="maintenance">Open queue</button></div><div class="ticket-list">${recentTickets || `<div class="empty-state"><div class="empty-icon">✳</div><h3>All quiet for now</h3><p>New maintenance requests will appear here.</p></div>`}</div></section><section class="panel"><div class="panel-head"><h2>Quick actions</h2></div><div class="quick-actions"><button class="action-card" data-page="properties"><span class="action-symbol">＋</span><span><strong>Add a property</strong><span>Grow your portfolio</span></span></button><button class="action-card" data-page="maintenance"><span class="action-symbol">⚒</span><span><strong>View maintenance</strong><span>Keep requests moving</span></span></button></div></section></div>
      </div>`;
  }

  function rentalListingForm() {
    const listing = state.activeListingData || {};
    const unit = state.units.find((item) => item.id === state.activeListingUnitId);
    if (!unit) return "";
    const photos = (listing.photos || []).map((photo) => `<figure class="listing-photo-thumb"><img src="${escapeHtml(photo.image)}" alt="${escapeHtml(photo.alt_text || "Listing photo")}"><button type="button" class="secondary-btn" data-delete-listing-photo="${escapeHtml(photo.id)}" aria-label="Remove photo">Remove</button></figure>`).join("");
    const remaining = Math.max(0, 8 - (listing.photos || []).length);
    const photoLabel = remaining === 8 ? "Add photos (up to 8)" : `Add photos (${remaining} remaining)`;
    return `<section class="panel listing-editor" id="listing-form-wrap"><div class="panel-head"><div><span class="page-kicker">Public rental advert · ${escapeHtml(unit.code)}</span><h2>${listing.exists ? "Edit listing" : "Create listing"}</h2></div><button class="secondary-btn" data-action="close-listing-form">Close</button></div><p class="subtle">Published listings are visible to anyone browsing HomeConnect. Use a general area rather than a private street address.</p><div id="listing-error" class="form-error" role="alert"></div><form id="listing-form"><div class="inline-form"><div class="field wide"><label for="listing-title">Listing title</label><input id="listing-title" name="title" required maxlength="180" value="${escapeHtml(listing.title || "")}" placeholder="e.g. Bright 2 bedroom apartment"></div><div class="field"><label for="listing-rent">Monthly rent (KES)</label><input id="listing-rent" name="monthly_rent" type="number" min="1" step="100" required value="${escapeHtml(listing.monthly_rent || "")}"></div><div class="field"><label for="listing-bedrooms">Bedrooms</label><input id="listing-bedrooms" name="bedrooms" type="number" min="0" max="99" value="${escapeHtml(listing.bedrooms ?? "")}"></div><div class="field"><label for="listing-bathrooms">Bathrooms</label><input id="listing-bathrooms" name="bathrooms" type="number" min="0" max="99" value="${escapeHtml(listing.bathrooms ?? "")}"></div><div class="field"><label for="listing-location">Public area</label><input id="listing-location" name="location_label" maxlength="140" value="${escapeHtml(listing.location_label || "")}" placeholder="e.g. Kilimani, near Yaya Centre"></div><div class="field"><label for="listing-phone">Public contact phone</label><input id="listing-phone" name="contact_phone" maxlength="32" type="tel" value="${escapeHtml(listing.contact_phone || "")}" placeholder="e.g. +254 7xx xxx xxx"></div><div class="field"><label for="listing-status">Visibility</label><select id="listing-status" name="status">${["draft", "published", "paused"].map((status) => `<option value="${status}" ${(listing.status || "draft") === status ? "selected" : ""}>${status[0].toUpperCase() + status.slice(1)}</option>`).join("")}</select></div><div class="field wide"><label for="listing-description">Description</label><textarea id="listing-description" name="description" maxlength="5000" rows="5" placeholder="Describe the home, features, and viewing arrangements.">${escapeHtml(listing.description || "")}</textarea></div><div class="field wide"><label for="listing-photos">${photoLabel}</label><input id="listing-photos" name="photos" type="file" accept="image/jpeg,image/png,image/webp" multiple ${remaining && listing.exists ? "" : "disabled"}><span class="subtle">Save the advert first. JPG, PNG, or WebP · up to 8 MB each · 8 photos per advert.</span></div>${photos ? `<div class="listing-photo-grid wide">${photos}</div>` : ""}<button class="primary-btn" type="submit">Save listing</button></div></form></section>`;
  }

  function propertiesView() {
    const cards = state.properties.map((property) => `<button class="property-card ${property.id === state.property?.id ? "selected" : ""}" data-property="${escapeHtml(property.id)}"><h3>${escapeHtml(property.name)}</h3><p>${escapeHtml([property.address, property.county].filter(Boolean).join(" · ") || "Add an address to help your team find it")}</p><div class="property-card-foot">${statusPill(property.status)}<span class="subtle">${state.units.filter((unit) => unit.property_id === property.id).length} units</span></div></button>`).join("");
    const units = state.units.filter((unit) => unit.property_id === state.property?.id);
    const rows = units.map((unit) => `<tr><td><strong>${escapeHtml(unit.code)}</strong>${unit.label ? `<br><span class="subtle">${escapeHtml(unit.label)}</span>` : ""}</td><td>${escapeHtml(unit.unit_type || "—")}</td><td>${statusPill(unit.status)}</td><td><div class="unit-actions"><button class="secondary-btn" data-action="show-tenant-invite" data-unit="${escapeHtml(unit.id)}">Invite tenant</button><button class="secondary-btn" data-action="show-unit-invitations" data-unit="${escapeHtml(unit.id)}">Invitations</button><button class="secondary-btn" data-action="show-listing-form" data-unit="${escapeHtml(unit.id)}">Rental advert</button></div></td></tr>`).join("");
    const selected = state.property;
    return `<section class="page-heading"><div><span class="page-kicker">Places you manage</span><h1>Properties</h1><p>Keep the details of each home and unit up to date.</p><a href="/listings" target="_blank" rel="noopener noreferrer">Preview public rental listings</a></div><button class="primary-btn" data-action="show-property-form">＋ Add property</button></section>
      <div id="property-form-wrap" class="panel" hidden><div class="panel-head"><h2>New property</h2></div><div id="property-error" class="form-error" role="alert"></div><form id="property-form" class="inline-form"><div class="field"><label for="property-name">Property name</label><input id="property-name" name="name" required maxlength="180" placeholder="e.g. Acacia Court"></div><div class="field"><label for="property-county">County</label><input id="property-county" name="county" maxlength="100" placeholder="e.g. Nairobi"></div><div class="field wide"><label for="property-address">Address</label><input id="property-address" name="address" maxlength="250" placeholder="Street, estate or area"></div><button class="primary-btn" type="submit">Save property</button></form></div>
      <div class="property-layout"><section class="panel"><div class="panel-head"><h2>Portfolio <span class="subtle">${state.properties.length}</span></h2></div><div class="property-list">${cards || `<div class="empty-state"><div class="empty-icon">⌂</div><h3>No properties yet</h3><p>Create a property to get started. Property access is enforced by your account permissions.</p></div>`}</div></section>
        <section class="panel">${selected ? `<div class="panel-head"><div><h2>${escapeHtml(selected.name)}</h2><span class="subtle">${escapeHtml([selected.address, selected.county].filter(Boolean).join(" · ") || "Location not added")}</span></div>${statusPill(selected.status)}</div><div class="section-toolbar"><h2>Units <span class="subtle">${units.length}</span></h2><button class="secondary-btn" data-action="show-unit-form">＋ Add unit</button></div><div class="unit-wrap">${units.length ? `<table class="unit-table"><thead><tr><th>Unit</th><th>Type</th><th>Status</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table>` : `<div class="empty-state"><div class="empty-icon">▦</div><h3>No units added</h3><p>Add units to track availability and open maintenance requests.</p></div>`}</div><div id="unit-form-wrap" class="notice" hidden><div id="unit-error" class="form-error" role="alert"></div><form id="unit-form" class="inline-form"><div class="field"><label for="unit-code">Unit code</label><input id="unit-code" name="code" required maxlength="80" placeholder="e.g. B-04"></div><div class="field"><label for="unit-type">Unit type</label><select id="unit-type" name="unit_type"><option value="">Select type</option><option value="Bedsitter">Bedsitter</option><option value="Single room">Single room</option><option value="1 bedroom">1 bedroom</option><option value="2 bedroom">2 bedroom</option><option value="3 bedroom">3 bedroom</option><option value="4 bedroom">4+ bedroom</option><option value="Other">Other</option></select></div><div class="field wide"><label for="unit-label">Label (optional)</label><input id="unit-label" name="label" maxlength="180" placeholder="Garden-facing apartment"></div><button class="primary-btn" type="submit">Save unit</button></form></div>${state.inviteUnitId ? `<div class="panel invite-form-panel"><div class="panel-head"><div><span class="page-kicker">New tenancy invitation</span><h2>Invite a tenant to ${escapeHtml(units.find((unit) => unit.id === state.inviteUnitId)?.code || "this unit")}</h2></div><button class="secondary-btn" data-action="cancel-tenant-invite">Cancel</button></div><p class="subtle">The person needs a HomeConnect account to sign in and accept. The end date is exclusive.</p><div id="invite-form-error" class="form-error" role="alert"></div><form id="tenant-invite-form" class="inline-form"><div class="field wide"><label for="invite-email">Tenant email</label><input id="invite-email" name="email" type="email" required autocomplete="email" placeholder="tenant@example.com"></div><div class="field"><label for="invite-start-date">Start date</label><input id="invite-start-date" name="start_date" type="date" required></div><div class="field"><label for="invite-end-date">End date (optional)</label><input id="invite-end-date" name="end_date" type="date"></div><div class="field"><label for="invite-rent">Monthly rent (${escapeHtml(state.organization.currency)})</label><input id="invite-rent" name="agreed_rent" type="number" min="0.01" step="0.01" required></div><div class="field"><label for="invite-due-day">Rent due day (1–31)</label><input id="invite-due-day" name="due_day" type="number" min="1" max="31" required></div><div class="field"><label for="invite-deposit">Deposit (${escapeHtml(state.organization.currency)})</label><input id="invite-deposit" name="deposit_amount" type="number" min="0" step="0.01" value="0.00"></div><div class="field wide"><label for="invite-deposit-terms">Deposit terms (optional)</label><textarea id="invite-deposit-terms" name="deposit_terms" maxlength="5000"></textarea></div><div class="field wide"><label for="invite-reference">Agreement reference (optional)</label><input id="invite-reference" name="agreement_reference" maxlength="200"></div><button class="primary-btn" type="submit">Send invitation</button></form></div>` : ""}` : `<div class="empty-state"><div class="empty-icon">⌂</div><h3>Select a property</h3><p>Choose a property on the left to view its units.</p></div>`}</section>
      </div>${state.activeListingUnitId ? rentalListingForm() : ""}${state.invitationManageUnitId ? invitationManagementView() : ""}`;
  }

  function invitationManagementView() {
    const unit = state.units.find((item) => item.id === state.invitationManageUnitId);
    const rows = state.unitInvitations.map((invite) => `<article class="ticket-item"><div><strong>${escapeHtml(invite.email)}</strong><span>${statusPill(invite.status)} · Email ${escapeHtml(invite.delivery_status)} · Expires ${escapeHtml(new Date(invite.expires_at).toLocaleDateString("en-KE", { day: "numeric", month: "short", year: "numeric" }))}</span><span>Proposed start ${escapeHtml(invite.start_date)}${invite.end_date ? ` · End ${escapeHtml(invite.end_date)} (exclusive)` : " · No end date"}</span></div>${invite.status === "pending" ? `<div class="unit-actions"><button class="secondary-btn" data-invitation-resend="${escapeHtml(invite.id)}">Resend link</button><button class="secondary-btn" data-invitation-cancel="${escapeHtml(invite.id)}">Cancel</button></div>` : ""}</article>`).join("");
    return `<section class="panel invitation-manage-panel"><div class="panel-head"><div><span class="page-kicker">Tenancy invitations</span><h2>${escapeHtml(unit?.code || "Unit")}</h2></div><button class="secondary-btn" data-action="close-unit-invitations">Close</button></div>${state.invitationListError ? `<div class="form-error visible" role="alert">${escapeHtml(state.invitationListError)}</div>` : `<div class="ticket-list">${rows || `<p class="subtle">No invitations have been sent for this unit.</p>`}</div>`}</section>`;
  }

  async function loadUnitInvitations(unitId) {
    state.invitationListError = "";
    state.unitInvitations = [];
    render();
    try { state.unitInvitations = await api(`/api/v1/units/${unitId}/invitations`); }
    catch (error) { state.unitInvitations = []; state.invitationListError = "You do not have permission to view invitations for this unit."; }
    render();
  }

  function teamView() {
    const members = state.members.map((member) => `<article class="ticket-item"><div><strong>${escapeHtml(member.email)}</strong><span>${escapeHtml(member.role)} · ${member.property_ids.length} assigned propert${member.property_ids.length === 1 ? "y" : "ies"}</span></div>${["manager", "caretaker"].includes(member.role) ? `<button class="secondary-btn" data-member-deactivate="${escapeHtml(member.id)}">Deactivate</button>` : ""}</article>`).join("");
    const properties = state.properties.map((property) => `<label class="check-option"><input type="checkbox" name="property_ids" value="${escapeHtml(property.id)}"> <span>${escapeHtml(property.name)}</span></label>`).join("");
    const canManage = state.teamAuthorized;
    return `<section class="page-heading"><div><span class="page-kicker">People and access</span><h1>Team</h1><p>Assign existing HomeConnect accounts to properties in this organization.</p></div></section>${state.teamError ? `<section class="panel"><div class="form-error visible" role="alert">${escapeHtml(state.teamError)}</div></section>` : ""}${canManage ? `<div class="content-grid"><section class="panel"><div class="panel-head"><h2>Organization members <span class="subtle">${state.members.length}</span></h2></div><div class="ticket-list">${members || `<p class="subtle">No active team members yet.</p>`}</div></section><section class="panel"><div class="panel-head"><h2>Assign a team member</h2></div><p class="subtle">The person must already have an account. This assigns access; it does not send an invitation email.</p><div id="team-form-error" class="form-error" role="alert"></div><form id="team-form" class="inline-form"><div class="field wide"><label for="member-email">Account email</label><input id="member-email" type="email" name="email" required autocomplete="email"></div><div class="field"><label for="member-role">Role</label><select id="member-role" name="role"><option value="manager">Manager</option><option value="caretaker">Caretaker</option></select></div><fieldset class="field wide"><legend>Property access</legend><div class="check-list">${properties || `<span class="subtle">Add a property before assigning team access.</span>`}</div></fieldset><button class="primary-btn" type="submit" ${properties ? "" : "disabled"}>Assign access</button></form></section></div>` : ""}`;
  }

  function mpesaSettingsView() {
    const settings = state.mpesaSettings || {};
    const status = settings.configured ? "M-Pesa credentials saved" : "Not configured";
    return `<section class="page-heading"><div><span class="page-kicker">Landlord settlement account</span><h1>M-Pesa</h1><p>Tenants will pay directly to this organization’s own Paybill or Till.</p></div></section>${state.mpesaSettingsError ? `<section class="panel"><div class="form-error visible" role="alert">${escapeHtml(state.mpesaSettingsError)}</div></section>` : ""}<section class="panel"><div class="panel-head"><div><h2>Daraja setup</h2><span class="subtle">${status}</span></div></div><p class="subtle">Create a Daraja app with the M-Pesa Express product. Credentials are encrypted in the database and never shown again after saving. Do not send them in chat.</p>${settings.callback_ready ? `<p class="provider-ready">Public HTTPS callback is configured.</p>` : `<p class="provider-not-ready">Payments cannot start until the app host has a public HTTPS callback URL. A local 127.0.0.1 address won’t receive Safaricom callbacks.</p>`}${settings.production_allowed ? "" : `<p class="subtle">Production is disabled on this host. Use Sandbox until live credentials and settlement approval are ready.</p>`}<div id="mpesa-settings-error" class="form-error" role="alert"></div><form id="mpesa-settings-form" class="inline-form"><div class="field"><label for="mpesa-environment">Daraja environment</label><select id="mpesa-environment" name="environment"><option value="sandbox" ${(settings.environment || "sandbox") === "sandbox" ? "selected" : ""}>Sandbox</option><option value="production" ${settings.environment === "production" ? "selected" : ""} ${settings.production_allowed ? "" : "disabled"}>Production</option></select></div><div class="field"><label for="mpesa-merchant-type">Account type</label><select id="mpesa-merchant-type" name="merchant_type"><option value="paybill" ${(settings.merchant_type || "paybill") === "paybill" ? "selected" : ""}>Paybill</option><option value="till" ${settings.merchant_type === "till" ? "selected" : ""}>Till / Buy Goods</option></select></div><div class="field"><label for="mpesa-business-shortcode">Business short code</label><input id="mpesa-business-shortcode" name="business_shortcode" inputmode="numeric" pattern="[0-9]{5,12}" required value="${escapeHtml(settings.business_shortcode || "")}"><span class="subtle">For Paybill, this is the Paybill number. For Till, use the HO/store short code Safaricom assigned.</span></div><div class="field"><label for="mpesa-shortcode">Paybill number or Till number</label><input id="mpesa-shortcode" name="shortcode" inputmode="numeric" pattern="[0-9]{5,12}" required value="${escapeHtml(settings.shortcode || "")}"></div><div class="field"><label for="mpesa-consumer-key">Consumer key</label><input id="mpesa-consumer-key" name="consumer_key" type="password" autocomplete="new-password" ${settings.configured ? "placeholder=\"Saved · leave blank to keep it\"" : "required"}></div><div class="field"><label for="mpesa-consumer-secret">Consumer secret</label><input id="mpesa-consumer-secret" name="consumer_secret" type="password" autocomplete="new-password" ${settings.configured ? "placeholder=\"Saved · leave blank to keep it\"" : "required"}></div><div class="field"><label for="mpesa-passkey">Lipa Na M-Pesa passkey</label><input id="mpesa-passkey" name="passkey" type="password" autocomplete="new-password" ${settings.configured ? "placeholder=\"Saved · leave blank to keep it\"" : "required"}></div><button class="primary-btn" type="submit">Save M-Pesa settings</button></form></section>`;
  }

  async function loadMpesaSettings() {
    state.mpesaSettingsError = "";
    state.mpesaSettings = null;
    try { state.mpesaSettings = await api(`/api/v1/organizations/${state.organization.id}/mpesa`); }
    catch (error) { state.mpesaSettingsError = error.message || "Could not load M-Pesa settings."; }
    render();
  }

  function tenanciesView() {
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Nairobi", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    const cards = state.tenancies.map((tenancy) => {
      const startPlusOne = new Date(`${tenancy.start_date}T00:00:00Z`);
      startPlusOne.setUTCDate(startPlusOne.getUTCDate() + 1);
      const endForm = state.activeTenancyEndId === tenancy.id ? `<form class="inline-form tenancy-end-form" data-tenancy-end-form="${escapeHtml(tenancy.id)}"><div id="tenancy-end-error" class="form-error" role="alert"></div><div class="field"><label for="tenancy-end-date-${escapeHtml(tenancy.id)}">Effective end date</label><input id="tenancy-end-date-${escapeHtml(tenancy.id)}" name="actual_end_date" type="date" min="${startPlusOne.toISOString().slice(0, 10)}" max="${today}" required></div><div class="field wide"><label for="tenancy-end-reason-${escapeHtml(tenancy.id)}">Reason or note (optional)</label><textarea id="tenancy-end-reason-${escapeHtml(tenancy.id)}" name="reason" maxlength="1000"></textarea></div><button class="primary-btn" type="submit">Record tenancy end</button><button class="secondary-btn" type="button" data-action="close-tenancy-end">Keep tenancy active</button></form>` : "";
      const endDate = tenancy.actual_end_date ? `Ended ${escapeHtml(tenancy.actual_end_date)}` : tenancy.end_date ? `Agreed end ${escapeHtml(tenancy.end_date)} (exclusive)` : "No agreed end date";
      const paymentPanel = state.activePaymentTenancyId === tenancy.id ? paymentRecordsView(tenancy) : "";
      return `<article class="panel tenancy-card"><div class="panel-head"><div><span class="page-kicker">${escapeHtml(tenancy.property_name)} · ${escapeHtml(tenancy.unit_code)}</span><h2>${escapeHtml(tenancy.tenant_email || tenancy.invited_email)}</h2></div>${statusPill(tenancy.status)}</div><div class="tenancy-facts"><div><span>Start</span><strong>${escapeHtml(tenancy.start_date)}</strong></div><div><span>End</span><strong>${endDate}</strong></div><div><span>Agreed rent</span><strong>${escapeHtml(tenancy.currency)} ${escapeHtml(tenancy.agreed_rent)} · due day ${escapeHtml(tenancy.due_day)}</strong></div><div><span>Deposit</span><strong>${escapeHtml(tenancy.currency)} ${escapeHtml(tenancy.deposit_amount)}</strong></div></div><div class="tenancy-actions">${tenancy.can_manage ? `${tenancy.can_activate ? `<button class="primary-btn" data-tenancy-activate="${escapeHtml(tenancy.id)}">Activate tenancy</button>` : ""}${tenancy.can_end ? `<button class="secondary-btn" data-tenancy-end="${escapeHtml(tenancy.id)}">Record end</button>` : ""}` : ""}<button class="secondary-btn" data-tenancy-payments="${escapeHtml(tenancy.id)}">${state.activePaymentTenancyId === tenancy.id ? "Refresh payments" : "Payment records"}</button></div>${endForm}${paymentPanel}</article>`;
    }).join("");
    return `<section class="page-heading"><div><span class="page-kicker">Occupancy and terms</span><h1>Tenancies</h1><p>Review tenancy terms and manual payment records. Entries are not provider-confirmed.</p></div></section><section class="tenancy-list">${cards || `<div class="panel empty-state"><div class="empty-icon">▤</div><h3>No tenancies to show</h3><p>Accepted tenant relationships and manager-visible tenancy records will appear here.</p></div>`}</section>`;
  }

  function paymentRecordsView(tenancy) {
    const records = state.paymentRecords.map((record) => `<article class="ticket-item"><div><strong>${escapeHtml(record.currency)} ${escapeHtml(record.amount)} · ${escapeHtml(record.payment_date)}</strong><span>${escapeHtml(record.method.replaceAll("_", " "))}${record.reference ? ` · Ref ${escapeHtml(record.reference)}` : ""}</span><span>${escapeHtml(record.record_type)}${record.recorded_by_name ? ` · recorded by ${escapeHtml(record.recorded_by_name)}` : ""}</span>${tenancy.can_manage && !record.reversed ? `<span>${escapeHtml(record.currency)} ${escapeHtml(record.available_amount)} unallocated</span>` : ""}${record.notes ? `<span>${escapeHtml(record.notes)}</span>` : ""}${record.source === "mpesa" ? `<span>M-Pesa refunds must be completed through Safaricom; this record does not send a refund.</span>` : ""}${record.reversed ? `<span>Reversed: ${escapeHtml(record.reversal_reason)}</span>` : ""}</div><div class="payment-record-actions">${tenancy.can_manage && !record.reversed && record.source !== "mpesa" && Number(record.available_amount) === Number(record.amount) ? `<button class="secondary-btn" data-payment-reverse="${escapeHtml(record.id)}">Reverse record</button>` : ""}<button class="secondary-btn" data-payment-receipt="${escapeHtml(record.id)}">Print receipt</button></div></article>`).join("");
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Nairobi", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    const attempt = state.activeMpesaAttempt;
    const attemptPanel = attempt ? `<div class="mpesa-attempt-status"><strong>M-Pesa status: ${escapeHtml(attempt.status)}</strong><span>${escapeHtml(attempt.message || "Waiting for Safaricom.")}</span>${attempt.receipt_number ? `<span>Receipt ${escapeHtml(attempt.receipt_number)}</span>` : ""}<button type="button" class="secondary-btn" data-action="refresh-mpesa-status">Refresh M-Pesa status</button></div>` : "";
    const tenantPayForm = !tenancy.can_manage && ["active", "scheduled_to_end"].includes(tenancy.status) ? `<form id="mpesa-payment-form" class="inline-form" data-tenancy="${escapeHtml(tenancy.id)}"><input type="hidden" name="idempotency_key" value="${crypto.randomUUID()}"><div class="field"><label for="mpesa-pay-amount">Amount (KES)</label><input id="mpesa-pay-amount" name="amount" type="number" min="1" max="250000" step="1" value="${Number.isInteger(Number(tenancy.agreed_rent)) ? escapeHtml(tenancy.agreed_rent) : ""}" required></div><div class="field"><label for="mpesa-pay-phone">M-Pesa phone number</label><input id="mpesa-pay-phone" name="phone_number" type="tel" inputmode="tel" autocomplete="tel" placeholder="0712 345 678" required></div><button class="primary-btn" type="submit">Pay with M-Pesa</button><p class="subtle wide">We’ll send a payment prompt to your phone. Only confirm the PIN after checking the amount and landlord shown by M-Pesa.</p></form>` : "";
    const rentQaPanel = root.dataset.rentQaEnabled === "true" ? `<section class="panel rent-qa-panel"><h3>Ask about this rent account</h3><p class="subtle">Answers use this tenancy’s rent records. Your question and these records are sent to the server’s configured AI provider. Check cited records before acting.</p><form id="rent-qa-form" class="inline-form" data-tenancy="${escapeHtml(tenancy.id)}"><div class="field wide"><label for="rent-question">Question</label><textarea id="rent-question" name="question" maxlength="2000" required placeholder="For example: what balance is overdue?"></textarea></div><button class="secondary-btn" type="submit">Ask rent question</button></form>${state.rentQaError ? `<div class="form-error visible" role="alert">${escapeHtml(state.rentQaError)}</div>` : ""}${state.rentQaAnswer ? `<article class="ticket-item"><div><strong>Answer</strong><span>${escapeHtml(state.rentQaAnswer.answer)}</span><span>Sources: ${state.rentQaAnswer.sources.map((source) => `${escapeHtml(source.label)} (${escapeHtml(source.id)})`).join(" · ") || "No supporting records returned"}</span></div></article>` : ""}</section>` : `<section class="panel rent-qa-panel"><h3>Rent Q&A</h3><p class="subtle">This feature is not configured on this server yet. Your records remain available above.</p></section>`;
    const obligations = state.rentObligations.map((obligation) => {
      const allocations = obligation.allocations.map((allocation) => `<article class="ticket-item"><div><strong>${escapeHtml(obligation.currency)} ${escapeHtml(allocation.amount)} allocated · ${escapeHtml(allocation.payment_date)}</strong><span>${allocation.reference ? `Ref ${escapeHtml(allocation.reference)}` : "Payment allocation"}${allocation.active ? " · Active" : ` · Reversed: ${escapeHtml(allocation.reversal_reason)}`}</span></div>${tenancy.can_manage && allocation.active ? `<button class="secondary-btn" data-allocation-reverse="${escapeHtml(allocation.id)}">Reverse allocation</button>` : ""}</article>`).join("");
      const availablePayments = state.paymentRecords.filter((record) => !record.reversed && Number(record.available_amount) > 0);
      const allocateForm = tenancy.can_manage && Number(obligation.outstanding_amount) > 0 && availablePayments.length ? `<form class="inline-form allocation-form" data-obligation="${escapeHtml(obligation.id)}"><input type="hidden" name="idempotency_key" value="${crypto.randomUUID()}"><div class="field"><label>Payment to allocate</label><select name="payment_id" required>${availablePayments.map((record) => `<option value="${escapeHtml(record.id)}">${escapeHtml(record.currency)} ${escapeHtml(record.available_amount)} available · ${escapeHtml(record.payment_date)}${record.reference ? ` · ${escapeHtml(record.reference)}` : ""}</option>`).join("")}</select></div><div class="field"><label>Amount (${escapeHtml(obligation.currency)})</label><input name="amount" type="number" min="0.01" step="0.01" max="${escapeHtml(obligation.outstanding_amount)}" value="${escapeHtml(Math.min(Number(obligation.outstanding_amount), Number(availablePayments[0].available_amount)).toFixed(2))}" required></div><button class="primary-btn" type="submit">Allocate to rent</button></form>` : "";
      const adjustForm = tenancy.can_manage ? `<form class="inline-form adjustment-form" data-obligation="${escapeHtml(obligation.id)}"><div class="field"><label>Adjustment (+/- ${escapeHtml(obligation.currency)})</label><input name="amount" type="number" step="0.01" required placeholder="e.g. -500 or 500"></div><div class="field"><label>Reason</label><input name="reason" maxlength="1000" required></div><button class="secondary-btn" type="submit">Add adjustment</button></form>` : "";
      return `<article class="panel obligation-card"><div class="panel-head"><div><h4>${escapeHtml(obligation.period_month)} ${obligation.is_partial ? "· Partial month" : ""}</h4><span class="subtle">Due ${escapeHtml(obligation.due_date)}${obligation.overdue ? " · Overdue" : ""}</span></div><strong>${escapeHtml(obligation.currency)} ${escapeHtml(obligation.outstanding_amount)} outstanding</strong></div><div class="tenancy-facts"><div><span>Rent charge</span><strong>${escapeHtml(obligation.currency)} ${escapeHtml(obligation.required_amount)}</strong></div><div><span>Allocated</span><strong>${escapeHtml(obligation.currency)} ${escapeHtml(obligation.allocated_amount)}</strong></div></div>${obligation.adjustments.length ? `<div class="ticket-list">${obligation.adjustments.map((item) => `<article class="ticket-item"><div><strong>Adjustment ${Number(item.amount) > 0 ? "+" : ""}${escapeHtml(item.amount)}</strong><span>${escapeHtml(item.reason)} · ${escapeHtml(item.created_by)}</span></div></article>`).join("")}</div>` : ""}${allocations ? `<div class="ticket-list">${allocations}</div>` : ""}${allocateForm}${adjustForm}</article>`;
    }).join("");
    const monthNow = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Nairobi", year: "numeric", month: "2-digit" }).format(new Date());
    const obligationForm = tenancy.can_manage ? `<form id="rent-obligation-form" class="inline-form" data-tenancy="${escapeHtml(tenancy.id)}"><div class="field"><label for="rent-period">Rent month</label><input id="rent-period" name="period_month" type="month" required value="${monthNow}"></div><div class="field"><label for="rent-partial-amount">Partial month amount (${escapeHtml(tenancy.currency)})</label><input id="rent-partial-amount" name="amount" type="number" min="0.01" step="0.01" placeholder="Only needed for a partial month"></div><button class="primary-btn" type="submit">Create rent charge</button><p class="subtle wide">Full months use the agreed rent automatically. For a month where the tenancy starts or ends mid-month, enter the amount you agreed with the tenant.</p></form>` : "";
    return `<section class="payment-record-panel"><div class="panel-head"><h3>Payment records <span class="subtle">${state.paymentRecords.length}</span></h3><button type="button" class="secondary-btn" data-action="close-payment-records">Close</button></div>${state.paymentError ? `<div class="form-error visible" role="alert">${escapeHtml(state.paymentError)}</div>` : ""}<div class="ticket-list">${records || `<p class="subtle">No payment records for this tenancy yet.</p>`}</div>${attemptPanel}${tenantPayForm}${tenancy.can_manage ? `<form id="payment-record-form" class="inline-form" data-tenancy="${escapeHtml(tenancy.id)}"><input type="hidden" name="idempotency_key" value="${crypto.randomUUID()}"><div class="field"><label for="payment-amount">Amount (${escapeHtml(tenancy.currency)})</label><input id="payment-amount" name="amount" type="number" min="0.01" step="0.01" required></div><div class="field"><label for="payment-method">Method</label><select id="payment-method" name="method"><option value="cash">Cash</option><option value="bank_transfer">Bank transfer</option><option value="mobile_money">Mobile money</option><option value="cheque">Cheque</option><option value="other">Other</option></select></div><div class="field"><label for="payment-date">Payment date</label><input id="payment-date" name="payment_date" type="date" value="${today}" required></div><div class="field"><label for="payment-reference">Reference (optional)</label><input id="payment-reference" name="reference" maxlength="160"></div><div class="field wide"><label for="payment-notes">Notes (optional)</label><input id="payment-notes" name="notes" maxlength="1000"></div><button class="primary-btn" type="submit">Record manual payment</button></form><p class="subtle">Manual records are not confirmed by a payment provider. M-Pesa records are added after a successful Safaricom callback.</p><div class="section-toolbar"><h3>Rent charges and balances</h3></div>${obligationForm}<div class="ticket-list">${obligations || `<p class="subtle">No rent charges yet. Create a monthly charge to track paid rent and arrears.</p>`}</div>` : `<div class="section-toolbar"><h3>Rent charges and balances</h3></div><div class="ticket-list">${obligations || `<p class="subtle">No rent charges have been added yet.</p>`}</div>`}${rentQaPanel}</section>`;
  }

  function maintenanceView() {
    const cards = state.tickets.map((ticket) => `<article class="ticket-card"><h3>${escapeHtml(ticket.category)} issue <span class="subtle">· ${escapeHtml(ticket.property_name || "Property")}</span></h3>${statusPill(ticket.status)}<p>${escapeHtml(ticket.description)}</p><div class="ticket-meta"><span>${escapeHtml(ticket.unit_code || "Unit")}</span><span>·</span><span>${escapeHtml(ticket.priority)} priority</span>${ticket.assigned_to_name ? `<span>· Assigned to ${escapeHtml(ticket.assigned_to_name)}</span>` : ""}<span>·</span><span>${escapeHtml(ticket.created_at ? new Date(ticket.created_at).toLocaleDateString("en-KE", { day: "numeric", month: "short", year: "numeric" }) : "")}</span></div><button class="secondary-btn" data-ticket="${escapeHtml(ticket.id)}">View request and updates</button></article>`).join("");
    const unitOptions = state.units.map((unit) => `<option value="${escapeHtml(unit.id)}">${escapeHtml(unit.code)} · ${escapeHtml(state.properties.find((property) => property.id === unit.property_id)?.name || "Property")}</option>`).join("");
    return `<section class="page-heading"><div><span class="page-kicker">Operations</span><h1>Maintenance</h1><p>Requests and updates for the properties you can access.</p></div><button class="primary-btn" data-action="show-ticket-form">＋ New request</button></section>
      <section id="ticket-form-wrap" class="panel" hidden><div class="panel-head"><div><h2>Report an issue</h2><span class="subtle">Only units linked to your active tenancy can be submitted.</span></div></div><div id="ticket-error" class="form-error" role="alert"></div><form id="ticket-form" class="inline-form"><div class="field"><label for="ticket-unit">Unit</label><select id="ticket-unit" name="unit_id" required>${unitOptions || `<option value="">No accessible units</option>`}</select></div><div class="field"><label for="ticket-category">Category</label><select id="ticket-category" name="category"><option value="plumbing">Plumbing</option><option value="electrical">Electrical</option><option value="structural">Structural</option><option value="security">Security</option><option value="appliance">Appliance</option><option value="pest">Pest control</option><option value="other">Other</option></select></div><div class="field"><label for="ticket-priority">Priority</label><select id="ticket-priority" name="priority"><option value="normal">Normal</option><option value="low">Low</option><option value="high">High</option><option value="urgent">Urgent</option></select></div><div class="field wide"><label for="ticket-description">What needs attention?</label><textarea id="ticket-description" name="description" maxlength="5000" required placeholder="Describe what happened and where it is in the unit."></textarea></div><div class="field wide"><label for="ticket-contact">Preferred contact (optional)</label><input id="ticket-contact" name="preferred_contact" maxlength="120" placeholder="A suitable time or contact detail"></div><div class="field wide"><label for="ticket-image">Photo (optional · JPEG, PNG or WebP · up to 8 MB)</label><input id="ticket-image" name="image" type="file" accept="image/jpeg,image/png,image/webp"></div><button class="primary-btn" type="submit" ${unitOptions ? "" : "disabled"}>Submit request</button></form><p class="subtle">Photos are resized and stripped of embedded metadata, and are visible only to you and authorized property staff. Notifications are not sent yet.</p></section>
      ${state.activeTicket ? ticketDetailView(state.activeTicket) : ""}<section class="panel"><div class="section-toolbar"><h2>Request queue <span class="subtle">${state.tickets.length}</span></h2><select id="ticket-filter" aria-label="Filter maintenance requests"><option value="all">All statuses</option><option value="open">Open</option><option value="resolved">Resolved</option></select></div><div id="ticket-list" class="ticket-list">${cards || `<div class="empty-state"><div class="empty-icon">✳</div><h3>No requests yet</h3><p>When a maintenance request is recorded, you’ll see its progress here.</p></div>`}</div></section>`;
  }

  function ticketDetailView(detail) {
    const ticket = detail.ticket;
    const comments = detail.comments.map((comment) => `<article class="ticket-item"><div><strong>${escapeHtml(comment.author_name)}</strong><span>${escapeHtml(comment.body)}</span></div><span class="subtle">${escapeHtml(comment.visibility === "internal" ? "Internal" : "Tenant visible")}</span></article>`).join("");
    const attachments = (ticket.attachments || []).map((attachment) => `<a href="${escapeHtml(attachment.url)}" target="_blank" rel="noopener noreferrer"><img src="${escapeHtml(attachment.url)}" alt="Maintenance issue photo" loading="lazy"><span>Open photo</span></a>`).join("");
    const transitions = detail.allowed_transitions.map((status) => `<option value="${escapeHtml(status)}">${escapeHtml(status.replaceAll("_", " "))}</option>`).join("");
    const assignees = detail.assignees.map((member) => `<option value="${escapeHtml(member.id)}" ${String(member.id) === String(ticket.assigned_to_id) ? "selected" : ""}>${escapeHtml(member.name)} · ${escapeHtml(member.role)}</option>`).join("");
    return `<section class="panel ticket-detail"><div class="panel-head"><div><span class="page-kicker">Request details</span><h2>${escapeHtml(ticket.category)} · ${escapeHtml(ticket.unit_code)}</h2></div><button class="secondary-btn" data-action="close-ticket-detail">Close</button></div><p>${escapeHtml(ticket.description)}</p><div class="ticket-meta">${statusPill(ticket.status)}<span>${escapeHtml(ticket.priority)} priority</span><span>${escapeHtml(ticket.property_name)}</span>${ticket.tenant_name ? `<span>Reported by ${escapeHtml(ticket.tenant_name)}</span>` : ""}${ticket.assigned_to_name ? `<span>Assigned to ${escapeHtml(ticket.assigned_to_name)}</span>` : ""}</div>${attachments ? `<div class="ticket-attachments" aria-label="Request photos">${attachments}</div>` : ""}<div id="ticket-detail-error" class="form-error" role="alert"></div>${detail.can_assign ? `<form id="ticket-assign-form" class="inline-form"><div class="field"><label for="ticket-assignee">Assign to property team</label><select id="ticket-assignee" name="membership_id" required ${assignees ? "" : "disabled"}>${assignees || `<option value="">No active team members assigned to this property</option>`}</select></div><button class="secondary-btn" type="submit" ${assignees ? "" : "disabled"}>${ticket.assigned_to_id ? "Change assignee" : "Assign request"}</button></form>` : ""}<div class="section-toolbar"><h3>Updates</h3></div><div class="ticket-list">${comments || `<p class="subtle">No updates yet.</p>`}</div><form id="ticket-comment-form" class="inline-form"><div class="field wide"><label for="ticket-comment">Add an update</label><textarea id="ticket-comment" name="body" required maxlength="3000" placeholder="Share a progress update"></textarea></div>${detail.staff ? `<div class="field"><label for="comment-visibility">Visibility</label><select id="comment-visibility" name="visibility"><option value="tenant">Tenant visible</option><option value="internal">Internal team note</option></select></div>` : ""}<button class="primary-btn" type="submit">Post update</button></form>${transitions ? `<form id="ticket-transition-form" class="inline-form"><div class="field"><label for="ticket-next-status">Update status</label><select id="ticket-next-status" name="status">${transitions}</select></div><div class="field wide" id="closure-note-field" hidden><label for="closure-note">Closure note</label><textarea id="closure-note" name="closure_note" maxlength="2000"></textarea></div><button class="secondary-btn" type="submit">Save status</button></form>` : ""}</section>`;
  }

  function render() {
    if (window.location.pathname.replace(/\/$/, "") === "/listings") { document.title = "Homes to rent · HomeConnect"; root.innerHTML = publicListingsView(); return; }
    if (state.invitationResult) { root.innerHTML = invitationResultView(); return; }
    if (!state.user) { root.innerHTML = loginView(); return; }
    if (state.invitationToken) {
      root.innerHTML = state.invitation ? invitationView() : `<main id="main" class="invitation-wrap"><section class="panel invitation-card"><span class="page-kicker">Tenancy invitation</span><h1>Invitation unavailable</h1><p class="form-error visible" role="alert">${escapeHtml(state.invitationError || "We could not load this invitation.")}</p><p class="subtle">Check that you signed in using the invited email address. If the issue continues, ask the property team to send a new link.</p><button class="secondary-btn" data-action="logout">Sign in with a different account</button></section></main>`;
      return;
    }
    if (!state.organizations.length) { root.innerHTML = orgSetupView(); return; }
    let content = state.page === "properties" ? propertiesView() : state.page === "maintenance" ? maintenanceView() : state.page === "tenancies" ? tenanciesView() : state.page === "payments" ? mpesaSettingsView() : state.page === "team" ? teamView() : overviewView();
    root.innerHTML = shell(content);
  }

  async function loadTeam() {
    state.teamError = "";
    try { state.members = await api(`/api/v1/organizations/${state.organization.id}/members`); state.teamAuthorized = true; }
    catch (error) { state.members = []; state.teamAuthorized = false; state.teamError = "Team management is available to organization owners. Ask an owner to manage these access settings."; }
    render();
  }

  function setFormError(id, message) {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = message;
    element.classList.add("visible");
  }

  root.addEventListener("click", async (event) => {
    const pageButton = event.target.closest("[data-page]");
    if (pageButton) { state.page = pageButton.dataset.page; if (state.page === "team") { await loadTeam(); } else if (state.page === "payments") { await loadMpesaSettings(); } else { render(); } return; }
    const deactivateButton = event.target.closest("[data-member-deactivate]");
    if (deactivateButton) {
      try { await api(`/api/v1/organizations/${state.organization.id}/members/${deactivateButton.dataset.memberDeactivate}`, { method: "DELETE" }); await loadTeam(); notify("Team access deactivated."); }
      catch (error) { notify(error.message); }
      return;
    }
    const resendButton = event.target.closest("[data-invitation-resend]");
    if (resendButton) {
      try {
        await api(`/api/v1/invitations/${resendButton.dataset.invitationResend}/resend`, { method: "POST" });
        notify("A fresh invitation link was sent.");
      } catch (error) { notify(error.message); }
      await loadUnitInvitations(state.invitationManageUnitId);
      return;
    }
    const cancelInviteButton = event.target.closest("[data-invitation-cancel]");
    if (cancelInviteButton) {
      if (!window.confirm("Cancel this pending invitation? Its current link will stop working.")) return;
      try {
        await api(`/api/v1/invitations/${cancelInviteButton.dataset.invitationCancel}/cancel`, { method: "POST" });
        notify("Invitation cancelled.");
      } catch (error) { notify(error.message); }
      await loadUnitInvitations(state.invitationManageUnitId);
      return;
    }
    const activateTenancyButton = event.target.closest("[data-tenancy-activate]");
    if (activateTenancyButton) {
      try {
        await api(`/api/v1/tenancies/${activateTenancyButton.dataset.tenancyActivate}/activate`, { method: "POST" });
        await loadWorkspace(); state.page = "tenancies"; render(); notify("Tenancy activated.");
      } catch (error) { notify(error.message); }
      return;
    }
    const paymentHistoryButton = event.target.closest("[data-tenancy-payments]");
    if (paymentHistoryButton) {
      state.activePaymentTenancyId = paymentHistoryButton.dataset.tenancyPayments;
      state.rentQaAnswer = null; state.rentQaError = "";
      state.activeMpesaAttempt = null;
      state.paymentError = "";
      try {
        [state.paymentRecords, state.rentObligations] = await Promise.all([
          api(`/api/v1/tenancies/${state.activePaymentTenancyId}/payments`),
          api(`/api/v1/tenancies/${state.activePaymentTenancyId}/rent-obligations`),
        ]);
      } catch (error) { state.paymentRecords = []; state.rentObligations = []; state.paymentError = error.message; }
      render(); return;
    }
    if (event.target.closest('[data-action="refresh-mpesa-status"]')) {
      if (!state.activeMpesaAttempt?.id) return;
      try {
        state.activeMpesaAttempt = await api(`/api/v1/mpesa-payments/${state.activeMpesaAttempt.id}`);
        state.paymentRecords = await api(`/api/v1/tenancies/${state.activePaymentTenancyId}/payments`);
        render();
      } catch (error) { notify(error.message); }
      return;
    }
    const receiptButton = event.target.closest("[data-payment-receipt]");
    if (receiptButton) {
      window.open(`/api/v1/payments/${receiptButton.dataset.paymentReceipt}/receipt`, "_blank", "noopener,noreferrer");
      return;
    }
    const reversePaymentButton = event.target.closest("[data-payment-reverse]");
    if (reversePaymentButton) {
      const reason = window.prompt("Why are you reversing this manual payment record?");
      if (!reason?.trim()) return;
      try {
        await api(`/api/v1/payments/${reversePaymentButton.dataset.paymentReverse}/reverse`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
        state.paymentRecords = await api(`/api/v1/tenancies/${state.activePaymentTenancyId}/payments`); render(); notify("Payment record reversed with an audit reason.");
      } catch (error) { notify(error.message); }
      return;
    }
    const reverseAllocationButton = event.target.closest("[data-allocation-reverse]");
    if (reverseAllocationButton) {
      const reason = window.prompt("Why are you reversing this rent allocation?");
      if (!reason?.trim()) return;
      try {
        await api(`/api/v1/payment-allocations/${reverseAllocationButton.dataset.allocationReverse}/reverse`, { method: "POST", body: JSON.stringify({ reason: reason.trim() }) });
        [state.paymentRecords, state.rentObligations] = await Promise.all([
          api(`/api/v1/tenancies/${state.activePaymentTenancyId}/payments`),
          api(`/api/v1/tenancies/${state.activePaymentTenancyId}/rent-obligations`),
        ]);
        render(); notify("Allocation reversed with an audit reason.");
      } catch (error) { notify(error.message); }
      return;
    }
    const endTenancyButton = event.target.closest("[data-tenancy-end]");
    if (endTenancyButton) {
      state.activeTenancyEndId = endTenancyButton.dataset.tenancyEnd;
      render(); document.querySelector(".tenancy-end-form input[type=date]")?.focus();
      return;
    }
    const propertyButton = event.target.closest("[data-property]");
    if (propertyButton) { state.property = state.properties.find((property) => property.id === propertyButton.dataset.property); state.invitationManageUnitId = null; state.unitInvitations = []; state.activeListingUnitId = null; state.activeListingData = null; render(); return; }
    const ticketButton = event.target.closest("[data-ticket]");
    if (ticketButton) {
      try { state.activeTicket = await api(`/api/v1/maintenance-tickets/${ticketButton.dataset.ticket}`); render(); document.querySelector(".ticket-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }); }
      catch (error) { notify(error.message); }
      return;
    }
    const actionButton = event.target.closest("[data-action]");
    const deletePhotoButton = event.target.closest("[data-delete-listing-photo]");
    if (deletePhotoButton) {
      try {
        await api(`/api/v1/listing-photos/${deletePhotoButton.dataset.deleteListingPhoto}`, { method: "DELETE" });
        state.activeListingData = await api(`/api/v1/units/${state.activeListingUnitId}/listing`);
        render(); notify("Photo removed.");
      } catch (error) { notify(error.message); }
      return;
    }
    if (!actionButton) return;
    if (actionButton.dataset.action === "show-listing-form") {
      state.activeListingUnitId = actionButton.dataset.unit;
      try {
        state.activeListingData = await api(`/api/v1/units/${state.activeListingUnitId}/listing`);
        render(); document.getElementById("listing-title")?.focus();
        document.getElementById("listing-form-wrap")?.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (error) { state.activeListingUnitId = null; state.activeListingData = null; notify(error.message); }
      return;
    }
    if (actionButton.dataset.action === "close-listing-form") { state.activeListingUnitId = null; state.activeListingData = null; render(); return; }
    if (actionButton.dataset.action === "close-payment-records") { state.activePaymentTenancyId = null; state.activeMpesaAttempt = null; state.paymentRecords = []; state.rentObligations = []; state.paymentError = ""; state.rentQaAnswer = null; state.rentQaError = ""; render(); return; }
    if (actionButton.dataset.action === "show-property-form") { document.getElementById("property-form-wrap").hidden = false; document.getElementById("property-name").focus(); }
    if (actionButton.dataset.action === "show-unit-form") { document.getElementById("unit-form-wrap").hidden = false; document.getElementById("unit-code").focus(); }
    if (actionButton.dataset.action === "show-tenant-invite") { state.inviteUnitId = actionButton.dataset.unit; render(); document.getElementById("invite-email")?.focus(); }
    if (actionButton.dataset.action === "cancel-tenant-invite") { state.inviteUnitId = null; render(); }
    if (actionButton.dataset.action === "show-unit-invitations") { state.invitationManageUnitId = actionButton.dataset.unit; await loadUnitInvitations(state.invitationManageUnitId); }
    if (actionButton.dataset.action === "close-unit-invitations") { state.invitationManageUnitId = null; state.unitInvitations = []; render(); }
    if (actionButton.dataset.action === "close-tenancy-end") { state.activeTenancyEndId = null; render(); }
    if (actionButton.dataset.action === "show-ticket-form") { document.getElementById("ticket-form-wrap").hidden = false; document.getElementById("ticket-unit")?.focus(); }
    if (actionButton.dataset.action === "close-ticket-detail") { state.activeTicket = null; render(); }
    if (actionButton.dataset.action === "decline-invitation") {
      try {
        await api("/api/v1/invitations/decline", { method: "POST", body: JSON.stringify({ token: state.invitationToken }) });
        state.invitationToken = ""; state.invitation = null; state.invitationResult = "declined"; render();
      } catch (error) { setFormError("invitation-error", error.message); }
      return;
    }
    if (actionButton.dataset.action === "finish-invitation") {
      if (state.invitationResult === "accepted") {
        state.invitationResult = ""; await loadWorkspace();
      } else {
        state.invitationResult = ""; state.user = null; render();
      }
      return;
    }
    if (actionButton.dataset.action === "logout") {
      try { await api("/api/v1/auth/logout", { method: "POST" }); } catch { /* The local view still clears if the session has expired. */ }
      state.user = null; state.organizations = []; state.members = []; state.teamAuthorized = false; render();
    }
  });

  root.addEventListener("change", async (event) => {
    if (event.target.id === "organization-select") {
      state.organization = state.organizations.find((item) => item.id === event.target.value);
      state.property = null;
      state.activeTicket = null;
      state.members = [];
      state.teamAuthorized = false;
      state.teamError = "";
      state.invitationManageUnitId = null;
      state.unitInvitations = [];
      try { await loadWorkspace(); } catch (error) { notify(error.message); }
      if (state.page === "team") await loadTeam();
      if (state.page === "payments") await loadMpesaSettings();
    }
    if (event.target.id === "ticket-filter") {
      const filter = event.target.value;
      document.querySelectorAll("#ticket-list .ticket-card").forEach((card) => {
        const status = card.querySelector(".status")?.textContent || "";
        card.hidden = filter === "resolved" ? !["resolved", "closed"].includes(status) : filter === "open" ? ["resolved", "closed", "cancelled"].includes(status) : false;
      });
    }
    if (event.target.id === "ticket-next-status") document.getElementById("closure-note-field").hidden = event.target.value !== "closed";
  });

  root.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const values = Object.fromEntries(new FormData(form).entries());
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    try {
      if (form.id === "rental-search-form") {
        await loadPublicListings(values);
      } else if (form.id === "login-form") {
        try {
          state.user = await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify(values) });
          if (state.invitationToken) await loadInvitation();
          else await loadWorkspace();
        } catch (error) { setFormError("login-error", error.message); }
      } else if (form.id === "invitation-accept-form") {
        try {
          await api("/api/v1/invitations/accept", { method: "POST", body: JSON.stringify({ token: state.invitationToken }) });
          state.invitationToken = ""; state.invitation = null; state.invitationResult = "accepted"; render();
        } catch (error) { setFormError("invitation-error", error.message); }
      } else if (form.id === "organization-form") {
        try { await api("/api/v1/organizations", { method: "POST", body: JSON.stringify(values) }); await loadWorkspace(); notify("Workspace created."); }
        catch (error) { setFormError("workspace-error", error.message); }
      } else if (form.id === "team-form") {
        const propertyIds = [...form.querySelectorAll('input[name="property_ids"]:checked')].map((input) => input.value);
        try {
          await api(`/api/v1/organizations/${state.organization.id}/members`, { method: "POST", body: JSON.stringify({ email: values.email, role: values.role, property_ids: propertyIds }) });
          await loadTeam(); notify("Team member assigned.");
        } catch (error) { setFormError("team-form-error", error.message); }
      } else if (form.id === "property-form") {
        try { await api(`/api/v1/organizations/${state.organization.id}/properties`, { method: "POST", body: JSON.stringify(values) }); await loadWorkspace(); state.page = "properties"; render(); notify("Property added."); }
        catch (error) { setFormError("property-error", error.message); }
      } else if (form.id === "unit-form") {
        try { await api(`/api/v1/properties/${state.property.id}/units`, { method: "POST", body: JSON.stringify(values) }); await loadWorkspace(); state.page = "properties"; render(); notify("Unit added."); }
        catch (error) { setFormError("unit-error", error.message); }
      } else if (form.id === "listing-form") {
        const selectedPhotos = [...form.querySelector('[name="photos"]').files];
        const payload = { ...values, bedrooms: values.bedrooms === "" ? null : Number(values.bedrooms), bathrooms: values.bathrooms === "" ? null : Number(values.bathrooms) };
        delete payload.photos;
        const method = state.activeListingData?.exists ? "PUT" : "POST";
        try {
          const saved = await api(`/api/v1/units/${state.activeListingUnitId}/listing`, { method, body: JSON.stringify(payload) });
          state.activeListingData = { ...saved, exists: true };
          const capacity = Math.max(0, 8 - (saved.photos || []).length);
          if (selectedPhotos.length > capacity) throw new Error(`Choose up to ${capacity} more photo${capacity === 1 ? "" : "s"}.`);
          for (const file of selectedPhotos) {
            const photoForm = new FormData(); photoForm.append("image", file); photoForm.append("alt_text", file.name.replace(/\.[^.]+$/, ""));
            await api(`/api/v1/units/${state.activeListingUnitId}/listing/photos`, { method: "POST", body: photoForm });
          }
          state.activeListingData = await api(`/api/v1/units/${state.activeListingUnitId}/listing`);
          render(); notify(selectedPhotos.length ? "Listing and photos saved." : saved.status === "published" ? "Rental listing published." : "Rental listing saved.");
        } catch (error) { setFormError("listing-error", error.message); }
      } else if (form.id === "payment-record-form") {
        try {
          await api(`/api/v1/tenancies/${form.dataset.tenancy}/payments`, { method: "POST", body: JSON.stringify(values) });
          state.paymentRecords = await api(`/api/v1/tenancies/${form.dataset.tenancy}/payments`);
          render(); notify("Manual payment recorded.");
        } catch (error) { notify(error.message); }
      } else if (form.id === "rent-qa-form") {
        state.rentQaError = ""; state.rentQaAnswer = null; render();
        try {
          state.rentQaAnswer = await api(`/api/v1/tenancies/${form.dataset.tenancy}/rent-questions`, { method: "POST", body: JSON.stringify({ question: values.question }) });
          render();
        } catch (error) { state.rentQaError = error.message; render(); }
      } else if (form.id === "rent-obligation-form") {
        const payload = { ...values };
        if (!payload.amount) delete payload.amount;
        try {
          await api(`/api/v1/tenancies/${form.dataset.tenancy}/rent-obligations`, { method: "POST", body: JSON.stringify(payload) });
          state.rentObligations = await api(`/api/v1/tenancies/${form.dataset.tenancy}/rent-obligations`);
          render(); notify("Rent charge saved.");
        } catch (error) { notify(error.message); }
      } else if (form.matches(".adjustment-form")) {
        try {
          await api(`/api/v1/rent-obligations/${form.dataset.obligation}/adjustments`, { method: "POST", body: JSON.stringify(values) });
          state.rentObligations = await api(`/api/v1/tenancies/${state.activePaymentTenancyId}/rent-obligations`);
          render(); notify("Rent adjustment added to the audit history.");
        } catch (error) { notify(error.message); }
      } else if (form.matches(".allocation-form")) {
        try {
          await api(`/api/v1/rent-obligations/${form.dataset.obligation}/allocations`, { method: "POST", body: JSON.stringify(values) });
          [state.paymentRecords, state.rentObligations] = await Promise.all([
            api(`/api/v1/tenancies/${state.activePaymentTenancyId}/payments`),
            api(`/api/v1/tenancies/${state.activePaymentTenancyId}/rent-obligations`),
          ]);
          render(); notify("Payment allocated to rent.");
        } catch (error) { notify(error.message); }
      } else if (form.id === "mpesa-payment-form") {
        try {
          state.activeMpesaAttempt = await api(`/api/v1/tenancies/${form.dataset.tenancy}/mpesa-payments`, { method: "POST", body: JSON.stringify(values) });
          state.paymentRecords = await api(`/api/v1/tenancies/${form.dataset.tenancy}/payments`);
          render(); notify("M-Pesa prompt sent. Check your phone and confirm the displayed amount and landlord.");
        } catch (error) { notify(error.message); }
      } else if (form.id === "mpesa-settings-form") {
        try {
          state.mpesaSettings = await api(`/api/v1/organizations/${state.organization.id}/mpesa`, { method: "PUT", body: JSON.stringify(values) });
          state.mpesaSettingsError = "";
          render(); notify("M-Pesa settings saved securely.");
        } catch (error) { setFormError("mpesa-settings-error", error.message); }
      } else if (form.id === "tenant-invite-form") {
        const payload = { ...values, end_date: values.end_date || null, deposit_amount: values.deposit_amount || "0.00" };
        try {
          const result = await api(`/api/v1/units/${state.inviteUnitId}/invitations`, { method: "POST", body: JSON.stringify(payload) });
          state.inviteUnitId = null; await loadWorkspace(); state.page = "properties"; render(); notify(`Invitation email ${result.delivery_status === "sent" ? "sent" : "queued"}.`);
        } catch (error) { setFormError("invite-form-error", error.message); }
      } else if (form.matches("[data-tenancy-end-form]")) {
        const tenancyId = form.dataset.tenancyEndForm;
        try {
          await api(`/api/v1/tenancies/${tenancyId}/end`, { method: "POST", body: JSON.stringify(values) });
          state.activeTenancyEndId = null; await loadWorkspace(); state.page = "tenancies"; render(); notify("Tenancy end recorded.");
        } catch (error) { setFormError("tenancy-end-error", error.message); }
      } else if (form.id === "ticket-form") {
        const ticketFormData = new FormData(form);
        if (!form.querySelector('[name="image"]').files.length) ticketFormData.delete("image");
        try { await api("/api/v1/maintenance-tickets", { method: "POST", body: ticketFormData }); await loadWorkspace(); state.page = "maintenance"; render(); notify("Request submitted."); }
        catch (error) { setFormError("ticket-error", error.message); }
      } else if (form.id === "ticket-comment-form") {
        try { await api(`/api/v1/maintenance-tickets/${state.activeTicket.ticket.id}/comments`, { method: "POST", body: JSON.stringify(values) }); state.activeTicket = await api(`/api/v1/maintenance-tickets/${state.activeTicket.ticket.id}`); render(); notify("Update posted."); }
        catch (error) { setFormError("ticket-detail-error", error.message); }
      } else if (form.id === "ticket-assign-form") {
        try { await api(`/api/v1/maintenance-tickets/${state.activeTicket.ticket.id}/assign`, { method: "POST", body: JSON.stringify(values) }); state.activeTicket = await api(`/api/v1/maintenance-tickets/${state.activeTicket.ticket.id}`); await loadWorkspace(); state.page = "maintenance"; render(); notify("Request assigned."); }
        catch (error) { setFormError("ticket-detail-error", error.message); }
      } else if (form.id === "ticket-transition-form") {
        try { await api(`/api/v1/maintenance-tickets/${state.activeTicket.ticket.id}/transition`, { method: "POST", body: JSON.stringify(values) }); state.activeTicket = await api(`/api/v1/maintenance-tickets/${state.activeTicket.ticket.id}`); await loadWorkspace(); state.page = "maintenance"; render(); notify("Request status updated."); }
        catch (error) { setFormError("ticket-detail-error", error.message); }
      }
    } finally {
      if (button?.isConnected) button.disabled = false;
    }
  });

  window.addEventListener("online", () => { state.online = true; render(); });
  window.addEventListener("offline", () => { state.online = false; render(); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js", { scope: "/" }).catch(() => {});
  boot();
})();
