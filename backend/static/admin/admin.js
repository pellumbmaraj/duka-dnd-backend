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
  };

  const clearError = (input, error) => {
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
  let previousCount = Number(window.sessionStorage.getItem('duka-application-count') || 0);
  let orderBaselineReady = document.body.hasAttribute('data-latest-order-id');
  let orderEventBaselineReady = document.body.hasAttribute('data-latest-order-event-id');
  let latestOrderId = Number(document.body.dataset.latestOrderId || 0);
  let latestOrderEventId = Number(document.body.dataset.latestOrderEventId || 0);
  let requestRunning = false;
  let orderRequestRunning = false;

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
        if (element.matches('.nav-count')) element.hidden = state.unreadCount === 0;
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

  const showOrderToast = (order) => {
    document.querySelector('[data-order-toast]')?.remove();
    const toast = document.createElement('aside');
    toast.className = 'application-toast order-toast';
    toast.dataset.orderToast = '';
    toast.setAttribute('role', 'status');
    toast.innerHTML = '<span class="toast-dot" aria-hidden="true"></span><div><small>POROSI E RE</small><strong></strong><span></span></div><a>Hap</a><button type="button" aria-label="Mbyll njoftimin">×</button>';
    toast.querySelector('strong').textContent = order.reference;
    toast.querySelector('div span').textContent = `${order.companyName} · ${order.itemCount} artikuj`;
    toast.querySelector('a').href = order.url;
    toast.querySelector('button').addEventListener('click', () => toast.remove());
    document.body.appendChild(toast);
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
    totalCell.textContent = new Intl.NumberFormat('en-IE', { style: 'currency', currency: order.currency || 'EUR' }).format(order.totalCents / 100);
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
        showOrderToast(state.orders[state.orders.length - 1]);
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

  refreshApplicationNotifications();
  refreshOrders();
  window.setInterval(refreshApplicationNotifications, 5000);
  window.setInterval(refreshOrders, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshApplicationNotifications();
      refreshOrders();
    }
  });
}

const operationsPanel = document.querySelector('[data-operations-panel]');
if (operationsPanel) {
  let operationRequestRunning = false;
  const rows = document.querySelector('[data-operation-rows]');
  const csrf = operationsPanel.dataset.operationCsrf;

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
    if (order.action) {
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
      document.querySelectorAll('[data-operation-count]').forEach((element) => { element.textContent = String(state.orders.length); });
      if (!rows) return;
      rows.replaceChildren();
      if (state.orders.length) state.orders.forEach((order) => rows.appendChild(operationRow(order)));
      else {
        const row = document.createElement('tr');
        row.dataset.operationEmpty = '';
        const cell = document.createElement('td');
        cell.colSpan = 6;
        cell.className = 'empty-state';
        cell.textContent = 'Nuk ka porosi që kërkojnë veprim.';
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
