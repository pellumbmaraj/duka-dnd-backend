'use strict';

const loginForm = document.querySelector('[data-login-form]');
if (loginForm) {
  const email = loginForm.querySelector('#email');
  const password = loginForm.querySelector('#password');
  const emailError = loginForm.querySelector('#email-error');
  const passwordError = loginForm.querySelector('#password-error');

  const showError = (input, error, message) => {
    input.classList.add('input-error');
    input.setAttribute('aria-invalid', 'true');
    error.textContent = message;
    window.clearTimeout(input._messageTimer);
    input._messageTimer = window.setTimeout(() => clearError(input, error), 5000);
  };

  const clearError = (input, error) => {
    window.clearTimeout(input._messageTimer);
    input.classList.remove('input-error');
    input.removeAttribute('aria-invalid');
    error.textContent = '';
  };

  email.addEventListener('input', () => clearError(email, emailError));
  password.addEventListener('input', () => clearError(password, passwordError));

  loginForm.addEventListener('submit', (event) => {
    clearError(email, emailError);
    clearError(password, passwordError);

    let firstInvalid = null;
    if (!email.value.trim()) {
      showError(email, emailError, 'Vendosni adresën e emailit.');
      firstInvalid = email;
    } else if (!email.validity.valid) {
      showError(email, emailError, 'Vendosni një adresë emaili të vlefshme.');
      firstInvalid = email;
    }

    if (!password.value) {
      showError(password, passwordError, 'Vendosni fjalëkalimin.');
      firstInvalid ||= password;
    }

    if (firstInvalid) {
      event.preventDefault();
      firstInvalid.focus();
    }
  });
}

const passwordToggle = document.querySelector('[data-password-toggle]');
if (passwordToggle) {
  passwordToggle.addEventListener('click', () => {
    const password = document.querySelector('#password');
    const showing = password.type === 'text';
    password.type = showing ? 'password' : 'text';
    passwordToggle.classList.toggle('showing-password', !showing);
    passwordToggle.setAttribute('aria-label', showing ? 'Shfaq fjalëkalimin' : 'Fshih fjalëkalimin');
    passwordToggle.setAttribute('aria-pressed', String(!showing));
  });
}

const transientElements = new WeakSet();
const dismissAfterFiveSeconds = (element) => {
  if (!(element instanceof HTMLElement) || transientElements.has(element)) return;
  transientElements.add(element);
  window.setTimeout(() => {
    element.style.opacity = '0';
    window.setTimeout(() => element.remove(), 250);
  }, 5000);
};
document.querySelectorAll('.notice, .application-toast').forEach(dismissAfterFiveSeconds);
new MutationObserver((records) => records.forEach((record) => record.addedNodes.forEach((node) => {
  if (!(node instanceof HTMLElement)) return;
  if (node.matches('.notice, .application-toast')) dismissAfterFiveSeconds(node);
  node.querySelectorAll?.('.notice, .application-toast').forEach(dismissAfterFiveSeconds);
}))).observe(document.body, { childList: true, subtree: true });

const menuToggle = document.querySelector('[data-menu-toggle]');
if (menuToggle) {
  menuToggle.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    menuToggle.setAttribute('aria-expanded', String(open));
    menuToggle.setAttribute('aria-label', open ? 'Mbyll navigimin' : 'Hap navigimin');
  });
}

document.querySelectorAll('[data-copy-value]').forEach((button) => {
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copyValue);
      button.textContent = 'U kopjua';
    } catch {
      button.textContent = 'Zgjidhni dhe kopjoni';
    }
  });
});

if (document.body.classList.contains('dashboard-page') && !document.body.classList.contains('operations-page')) {
  const ensureSidebarCounter = (path, dataAttribute) => {
    const link = [...document.querySelectorAll('.side-nav .nav-item')].find((item) => {
      try {
        return new URL(item.href, window.location.origin).pathname === path;
      } catch {
        return false;
      }
    });
    if (!link) return;

    let counter = link.querySelector('.nav-count');
    if (!counter) {
      counter = document.createElement('small');
      counter.className = 'nav-count';
      counter.textContent = '0';
      link.appendChild(counter);
    }
    counter.setAttribute(dataAttribute, '');
    counter.hidden = false;
  };

  ensureSidebarCounter('/admin/applications', 'data-application-count');
  ensureSidebarCounter('/admin/clients', 'data-pending-client-count');
  ensureSidebarCounter('/admin/orders', 'data-order-alert-count');

  let previousCount = Number(window.sessionStorage.getItem('duka-application-count') || 0);
  let orderBaselineReady = document.body.hasAttribute('data-latest-order-id');
  let orderEventBaselineReady = document.body.hasAttribute('data-latest-order-event-id');
  let latestOrderId = Number(document.body.dataset.latestOrderId || 0);
  let latestOrderEventId = Number(document.body.dataset.latestOrderEventId || 0);
  let requestRunning = false;
  let businessRequestRunning = false;
  let businessBaselineReady = false;
  let latestAdditionalBusinessId = 0;
  let orderRequestRunning = false;
  let notificationRequestRunning = false;
  let notificationInitialized = false;
  let latestNotificationId = 0;
  let notificationItems = [];
  let deliveredNotificationId = Number(window.sessionStorage.getItem('duka-admin-notification-delivered') || 0);
  let seenNotificationId = Number(window.sessionStorage.getItem('duka-admin-notification-seen') || 0);

  const notificationCenter = document.createElement('div');
  notificationCenter.className = 'notification-center';
  notificationCenter.innerHTML = '<button class="notification-toggle" type="button" aria-label="Hap njoftimet" aria-expanded="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg><b hidden>0</b></button><section class="notification-panel" hidden><header><div><small>NJOFTIMET</small><strong>Aktiviteti i fundit</strong></div><button type="button" aria-label="Mbyll njoftimet">×</button></header><div class="notification-feed"></div></section>';
  const topbar = document.querySelector('.topbar');
  if (topbar) topbar.insertBefore(notificationCenter, topbar.querySelector('.topbar-user'));
  else {
    notificationCenter.classList.add('notification-center-floating');
    document.body.appendChild(notificationCenter);
  }
  const notificationToggle = notificationCenter.querySelector('.notification-toggle');
  const notificationPanel = notificationCenter.querySelector('.notification-panel');
  const notificationFeed = notificationCenter.querySelector('.notification-feed');
  const notificationBadge = notificationToggle.querySelector('b');

  const renderNotificationCenter = () => {
    notificationFeed.replaceChildren();
    const newest = notificationItems.slice(-20).reverse();
    if (!newest.length) {
      const empty = document.createElement('p');
      empty.className = 'notification-empty';
      empty.textContent = 'Ende nuk ka njoftime.';
      notificationFeed.appendChild(empty);
    } else newest.forEach((item) => {
      const link = document.createElement('a');
      const title = document.createElement('strong');
      const message = document.createElement('span');
      const date = document.createElement('small');
      link.href = item.url;
      if (item.eventType === 'order_created') link.classList.add('notification-order');
      title.textContent = item.title;
      message.textContent = item.message;
      date.textContent = item.createdAt;
      link.append(title, message, date);
      notificationFeed.appendChild(link);
    });
    const unread = notificationItems.filter((item) => Number(item.id) > seenNotificationId).length;
    notificationBadge.textContent = unread > 99 ? '99+' : String(unread);
    notificationBadge.hidden = unread === 0;
  };

  const closeNotificationCenter = () => {
    notificationPanel.hidden = true;
    notificationToggle.setAttribute('aria-expanded', 'false');
  };

  notificationToggle.addEventListener('click', () => {
    const opening = notificationPanel.hidden;
    notificationPanel.hidden = !opening;
    notificationToggle.setAttribute('aria-expanded', String(opening));
    if (opening) {
      seenNotificationId = latestNotificationId;
      window.sessionStorage.setItem('duka-admin-notification-seen', String(seenNotificationId));
      renderNotificationCenter();
    }
  });
  notificationPanel.querySelector('header button').addEventListener('click', closeNotificationCenter);
  document.addEventListener('click', (event) => {
    if (!notificationCenter.contains(event.target)) closeNotificationCenter();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeNotificationCenter();
  });

  const showApplicationToast = (application) => {
    document.querySelector('[data-application-toast]')?.remove();
    const toast = document.createElement('aside');
    toast.className = 'application-toast';
    toast.dataset.applicationToast = '';
    toast.setAttribute('role', 'status');
    toast.innerHTML = '<span class="toast-dot" aria-hidden="true"></span><div><small>KËRKESË E RE PËR LLOGARI</small><strong></strong><span>Hapni të dhënat e dërguara të biznesit.</span></div><a>Hap</a><button type="button" aria-label="Mbyll njoftimin">×</button>';
    toast.querySelector('strong').textContent = application.companyName;
    toast.querySelector('a').href = `/admin/applications/${encodeURIComponent(application.id)}`;
    toast.querySelector('button').addEventListener('click', () => toast.remove());
    document.body.appendChild(toast);
  };

  const refreshApplicationNotifications = async () => {
    if (requestRunning || document.hidden) return;
    requestRunning = true;
    try {
      const response = await fetch('/admin/applications/live', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      const state = await response.json();
      document.querySelectorAll('[data-application-count]').forEach((element) => {
        element.textContent = String(state.unreadCount);
        if (element.matches('.nav-count')) element.hidden = false;
      });
      document.querySelectorAll('[data-verification-count]').forEach((element) => {
        element.textContent = String(state.verificationCount);
      });
      if (state.unreadCount > previousCount && state.latest) showApplicationToast(state.latest);
      previousCount = state.unreadCount;
      window.sessionStorage.setItem('duka-application-count', String(state.unreadCount));
    } catch {
      // The next interval retries without interrupting admin work.
    } finally {
      requestRunning = false;
    }
  };

  const refreshAdditionalBusinessNotifications = async () => {
    if (businessRequestRunning || document.hidden) return;
    businessRequestRunning = true;
    try {
      const response = await fetch(`/admin/businesses/live?after=${encodeURIComponent(latestAdditionalBusinessId)}`, {
        credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store',
      });
      if (!response.ok) return;
      const state = await response.json();
      document.querySelectorAll('[data-pending-client-count]').forEach((element) => {
        element.textContent = String(state.pendingCount);
        if (element.matches('.nav-count')) element.hidden = false;
      });
      if (!businessBaselineReady) {
        latestAdditionalBusinessId = Number(state.latestId || 0);
        businessBaselineReady = true;
        return;
      }
      if (state.businesses.length) {
        latestAdditionalBusinessId = Number(state.businesses[state.businesses.length - 1].id);
      } else latestAdditionalBusinessId = Math.max(latestAdditionalBusinessId, Number(state.latestId || 0));
    } catch {
      // The next interval retries without interrupting admin work.
    } finally {
      businessRequestRunning = false;
    }
  };

  const createOrderRow = (order) => {
    const row = document.createElement('tr');
    row.dataset.orderId = String(order.id);

    const referenceCell = document.createElement('td');
    const reference = document.createElement('a');
    reference.className = 'record-link';
    reference.href = order.url;
    reference.textContent = order.reference;
    referenceCell.appendChild(reference);

    const businessCell = document.createElement('td');
    businessCell.append(document.createTextNode(order.companyName));
    const address = document.createElement('small');
    address.textContent = order.deliveryAddress;
    businessCell.appendChild(address);

    const itemsCell = document.createElement('td');
    itemsCell.textContent = String(order.itemCount);
    const totalCell = document.createElement('td');
    totalCell.textContent = new Intl.NumberFormat('sq-AL', { style: 'currency', currency: order.currency || 'ALL' }).format(order.totalCents / 100);
    const statusCell = document.createElement('td');
    const status = document.createElement('span');
    const allowedStatuses = ['submitted', 'confirmed', 'processing', 'shipped', 'completed', 'cancelled'];
    const statusName = allowedStatuses.includes(order.status) ? order.status : 'submitted';
    const statusLabels = { submitted: 'Dërguar', confirmed: 'Konfirmuar', processing: 'Në përpunim', shipped: 'Në transport', completed: 'Përfunduar', cancelled: 'Anuluar' };
    status.className = `status status-${statusName}`;
    status.textContent = statusLabels[statusName];
    statusCell.appendChild(status);
    const receivedCell = document.createElement('td');
    receivedCell.textContent = order.createdAt;
    row.append(referenceCell, businessCell, itemsCell, totalCell, statusCell, receivedCell);
    return row;
  };

  const showOrderStatusToast = (change) => {
    document.querySelector('[data-order-toast]')?.remove();
    const toast = document.createElement('aside');
    toast.className = 'application-toast order-toast';
    toast.dataset.orderToast = '';
    toast.setAttribute('role', 'status');
    toast.innerHTML = '<span class="toast-dot" aria-hidden="true"></span><div><small>STATUSI I POROSISË U PËRDITËSUA</small><strong></strong><span></span></div><a>Hap</a><button type="button" aria-label="Mbyll njoftimin">×</button>';
    toast.querySelector('strong').textContent = change.reference;
    const statusLabels = { submitted: 'Dërguar', confirmed: 'Konfirmuar', processing: 'Në përpunim', shipped: 'Në transport', completed: 'Përfunduar', cancelled: 'Anuluar' };
    toast.querySelector('div span').textContent = `Statusi: ${statusLabels[change.status] || change.status}`;
    toast.querySelector('a').href = change.url;
    toast.querySelector('button').addEventListener('click', () => toast.remove());
    document.body.appendChild(toast);
  };

  const refreshOrders = async () => {
    if (orderRequestRunning || document.hidden) return;
    orderRequestRunning = true;
    try {
      const response = await fetch(`/admin/orders/live?after=${encodeURIComponent(latestOrderId)}&afterEvent=${encodeURIComponent(latestOrderEventId)}`, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) return;
      const state = await response.json();
      document.querySelectorAll('[data-order-count]').forEach((element) => { element.textContent = String(state.last30Days); });
      document.querySelectorAll('[data-order-total]').forEach((element) => { element.textContent = String(state.total); });
      document.querySelectorAll('[data-order-alert-count]').forEach((element) => {
        element.textContent = String(state.alertCount ?? 0);
        element.hidden = false;
      });
      if (!orderBaselineReady || !orderEventBaselineReady) {
        latestOrderId = Number(state.latestId || 0);
        latestOrderEventId = Number(state.latestEventId || 0);
        orderBaselineReady = true;
        orderEventBaselineReady = true;
        document.body.dataset.latestOrderId = String(latestOrderId);
        document.body.dataset.latestOrderEventId = String(latestOrderEventId);
        return;
      }
      const rows = document.querySelector('[data-order-rows]');
      if (rows && state.orders.length) {
        rows.querySelector('[data-order-empty]')?.remove();
        state.orders.forEach((order) => {
          if (!rows.querySelector(`[data-order-id="${order.id}"]`)) rows.prepend(createOrderRow(order));
        });
      }
      state.changes.forEach((change) => {
        const row = document.querySelector(`[data-order-id="${change.orderId}"]`);
        const badge = row?.querySelector('.status');
        if (badge) {
          badge.className = `status status-${change.status}`;
          const labels = { submitted: 'Dërguar', confirmed: 'Konfirmuar', processing: 'Në përpunim', shipped: 'Në transport', completed: 'Përfunduar', cancelled: 'Anuluar' };
          badge.textContent = labels[change.status] || change.status;
        }
      });
      if (state.orders.length) {
        latestOrderId = Number(state.orders[state.orders.length - 1].id);
      } else {
        latestOrderId = Math.max(latestOrderId, Number(state.latestId || 0));
      }
      if (!state.orders.length && state.changes.length) showOrderStatusToast(state.changes[state.changes.length - 1]);
      latestOrderEventId = Number(state.nextEventId || latestOrderEventId);
      document.body.dataset.latestOrderId = String(latestOrderId);
      document.body.dataset.latestOrderEventId = String(latestOrderEventId);
    } catch {
      // The next interval retries without interrupting admin work.
    } finally {
      orderRequestRunning = false;
    }
  };

  const showGeneralNotification = (item) => {
    document.querySelector('[data-general-notification-toast]')?.remove();
    const toast = document.createElement('aside');
    toast.className = 'application-toast';
    toast.dataset.generalNotificationToast = '';
    toast.setAttribute('role', 'status');
    const notificationHeadings = {
      business_created: 'BIZNES I RI PËR VERIFIKIM',
      order_created: 'POROSI E RE',
    };
    const heading = notificationHeadings[item.eventType] || 'AKTIVITET I RI';
    toast.innerHTML = `<span class="toast-dot" aria-hidden="true"></span><div><small>${heading}</small><strong></strong><span></span></div><a>Hap</a><button type="button" aria-label="Mbyll njoftimin">×</button>`;
    toast.querySelector('strong').textContent = item.title;
    toast.querySelector('div span').textContent = item.message;
    toast.querySelector('a').href = item.url;
    toast.querySelector('button').addEventListener('click', () => toast.remove());
    document.body.appendChild(toast);
  };

  const addNotificationRow = (item) => {
    const list = document.querySelector('[data-notification-list]');
    if (!list) return;
    list.querySelector('[data-notification-empty]')?.remove();
    const row = document.createElement('div');
    const copy = document.createElement('span');
    const title = document.createElement('strong');
    const message = document.createElement('small');
    const date = document.createElement('strong');
    const action = document.createElement('a');
    const actions = document.createElement('span');
    title.textContent = item.title;
    message.textContent = item.message;
    date.textContent = item.createdAt;
    action.className = 'table-action';
    action.href = item.url;
    action.textContent = 'Hap';
    actions.className = 'notification-actions';
    actions.append(date, action);
    copy.append(title, message);
    row.append(copy, actions);
    list.prepend(row);
    while (list.children.length > 8) list.lastElementChild?.remove();
  };

  const refreshGeneralNotifications = async () => {
    if (notificationRequestRunning || document.hidden) return;
    notificationRequestRunning = true;
    try {
      const after = notificationInitialized ? latestNotificationId : 0;
      const response = await fetch(`/admin/notifications/live?after=${encodeURIComponent(after)}`, { credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!response.ok) return;
      const state = await response.json();
      const serverLatestId = Number(state.latestId || 0);
      if (serverLatestId < latestNotificationId || serverLatestId < seenNotificationId || serverLatestId < deliveredNotificationId) {
        latestNotificationId = 0;
        seenNotificationId = 0;
        deliveredNotificationId = 0;
        notificationItems = [];
        window.sessionStorage.setItem('duka-admin-notification-seen', '0');
        window.sessionStorage.setItem('duka-admin-notification-delivered', '0');
      }
      const incoming = state.notifications.filter((item) => !notificationItems.some((saved) => Number(saved.id) === Number(item.id)));
      if (!notificationInitialized) notificationItems = incoming.slice(-20);
      else notificationItems = [...notificationItems, ...incoming].slice(-20);
      if (notificationInitialized) incoming.forEach(addNotificationRow);
      renderNotificationCenter();
      const specializedEvents = new Set(['account_application', 'order_updated']);
      const toastItems = incoming.filter((item) => Number(item.id) > deliveredNotificationId && !specializedEvents.has(item.eventType));
      if (toastItems.length) showGeneralNotification(toastItems[toastItems.length - 1]);
      latestNotificationId = serverLatestId;
      deliveredNotificationId = Math.max(deliveredNotificationId, serverLatestId);
      notificationInitialized = true;
      window.sessionStorage.setItem('duka-admin-notification-delivered', String(deliveredNotificationId));
      document.body.dataset.latestNotificationId = String(latestNotificationId);
    } catch {
      // The next interval retries without interrupting admin work.
    } finally {
      notificationRequestRunning = false;
    }
  };

  refreshApplicationNotifications();
  refreshAdditionalBusinessNotifications();
  refreshOrders();
  refreshGeneralNotifications();
  window.setInterval(refreshApplicationNotifications, 5000);
  window.setInterval(refreshAdditionalBusinessNotifications, 5000);
  window.setInterval(refreshOrders, 5000);
  window.setInterval(refreshGeneralNotifications, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshApplicationNotifications();
      refreshAdditionalBusinessNotifications();
      refreshOrders();
      refreshGeneralNotifications();
    }
  });
}

const operationsPanel = document.querySelector('[data-operations-panel]');
if (operationsPanel) {
  let operationRequestRunning = false;
  const rows = document.querySelector('[data-operation-rows]');
  const csrf = operationsPanel.dataset.operationCsrf;
  const operationRole = operationsPanel.dataset.operationRole;

  const operationRow = (order) => {
    const row = document.createElement('tr');
    row.dataset.operationOrder = String(order.id);
    const referenceCell = document.createElement('td');
    const reference = document.createElement('a');
    reference.className = 'record-link';
    reference.href = order.url;
    reference.textContent = order.reference;
    const created = document.createElement('small');
    created.textContent = order.createdAt;
    referenceCell.append(reference, created);
    if (order.hasPendingChange) {
      const pending = document.createElement('small');
      pending.className = 'pending-change-label';
      pending.textContent = 'Ndryshim në pritje';
      referenceCell.appendChild(pending);
    }
    const businessCell = document.createElement('td');
    const company = document.createElement('strong');
    company.textContent = order.companyName;
    const phone = document.createElement('small');
    phone.textContent = order.phone;
    businessCell.append(company, phone);
    const addressCell = document.createElement('td');
    addressCell.textContent = order.deliveryAddress;
    const itemsCell = document.createElement('td');
    itemsCell.textContent = String(order.itemCount);
    const statusCell = document.createElement('td');
    const status = document.createElement('span');
    status.className = `status status-${order.status}`;
    const statusLabels = { submitted: 'Dërguar', confirmed: 'Konfirmuar', processing: 'Në përpunim', shipped: 'Në transport', completed: 'Përfunduar', cancelled: 'Anuluar' };
    status.textContent = statusLabels[order.status] || order.status;
    statusCell.appendChild(status);
    const actionCell = document.createElement('td');
    if (order.hasPendingChange && operationRole === 'packing') {
      const review = document.createElement('a');
      review.className = 'table-action';
      review.href = order.url;
      review.textContent = 'Shqyrto ndryshimin';
      actionCell.appendChild(review);
    } else if (order.action) {
      const form = document.createElement('form');
      form.className = 'operation-action';
      form.method = 'post';
      form.action = order.actionUrl;
      for (const [name, value] of [['csrf_token', csrf], ['status', order.action.status]]) {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        input.value = value;
        form.appendChild(input);
      }
      const button = document.createElement('button');
      button.className = 'table-action';
      button.type = 'submit';
      button.textContent = order.action.label;
      form.appendChild(button);
      actionCell.appendChild(form);
    } else {
      const view = document.createElement('a');
      view.className = 'record-link';
      view.href = order.url;
      view.textContent = 'Shiko';
      actionCell.appendChild(view);
    }
    row.append(referenceCell, businessCell, addressCell, itemsCell, statusCell, actionCell);
    return row;
  };

  const refreshOperationQueue = async () => {
    if (operationRequestRunning || document.hidden) return;
    operationRequestRunning = true;
    try {
      const response = await fetch('/operations/live', { credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store' });
      if (!response.ok) return;
      const state = await response.json();
      document.querySelectorAll('[data-operation-count]').forEach((element) => { element.textContent = String(state.actionCount); });
      if (!rows) return;
      rows.replaceChildren();
      if (state.orders.length) state.orders.forEach((order) => rows.appendChild(operationRow(order)));
      else {
        const row = document.createElement('tr');
        row.dataset.operationEmpty = '';
        const cell = document.createElement('td');
        cell.colSpan = 6;
        cell.className = 'empty-state';
        cell.textContent = 'Nuk ka porosi.';
        row.appendChild(cell);
        rows.appendChild(row);
      }
    } catch {
      // The next interval retries without interrupting operational work.
    } finally {
      operationRequestRunning = false;
    }
  };

  window.setInterval(refreshOperationQueue, 5000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshOperationQueue(); });
}
