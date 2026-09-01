const tg = window.Telegram?.WebApp;
const state = { categories: [], products: [], filter: "all", query: "" };

if (tg) { tg.ready(); tg.expand(); }

const message = document.getElementById("message");
function showMessage(text, error = false) {
  message.textContent = text;
  message.classList.toggle("hidden", !text);
  message.style.color = error ? "var(--danger)" : "var(--brand)";
}

function authHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (tg?.initData) headers.Authorization = `tma ${tg.initData}`;
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "تعذر الاتصال بالخادم");
  }
  return response.json();
}

function flattenCatalog(data) {
  const products = [];
  for (const category of data.categories) {
    for (const sub of category.sub_categories) {
      for (const product of sub.products) {
        products.push({ ...product, categoryId: category.id, categoryName: category.name, subName: sub.name });
      }
    }
  }
  return products;
}

function renderChips() {
  const chips = document.getElementById("chips");
  chips.innerHTML = `<button class="chip ${state.filter === "all" ? "active" : ""}" data-filter="all">الكل</button>`;
  for (const category of state.categories) {
    const button = document.createElement("button");
    button.className = `chip ${state.filter === String(category.id) ? "active" : ""}`;
    button.dataset.filter = category.id;
    button.textContent = `${category.emoji} ${category.name}`;
    chips.appendChild(button);
  }
  chips.querySelectorAll(".chip").forEach((button) => {
    button.onclick = () => { state.filter = String(button.dataset.filter); renderChips(); renderProducts(); };
  });
}

function renderProducts() {
  const root = document.getElementById("catalog");
  const empty = document.getElementById("empty");
  root.innerHTML = "";
  const query = state.query.toLocaleLowerCase();
  const products = state.products.filter((product) => {
    const matchesCategory = state.filter === "all" || String(product.categoryId) === state.filter;
    const matchesQuery = !query || `${product.name} ${product.description || ""} ${product.subName}`.toLocaleLowerCase().includes(query);
    return matchesCategory && matchesQuery;
  });
  empty.classList.toggle("hidden", products.length !== 0);
  let currentCategory = null;
  for (const product of products) {
    if (currentCategory !== product.categoryId) {
      currentCategory = product.categoryId;
      const title = document.createElement("h2");
      title.className = "category-title";
      title.textContent = product.categoryName;
      root.appendChild(title);
    }
    const card = document.createElement("article");
    card.className = "product";
    const promo = product.promotion;
    const price = promo ? promo.discounted_price : product.price_usd;
    card.innerHTML = `
      ${promo ? `<span class="badge">🔥 ${escapeHtml(promo.name)}</span>` : ""}
      <h3>${escapeHtml(product.name)}</h3>
      <p>${escapeHtml(product.description || "خدمة رقمية جاهزة للطلب")}</p>
      <div class="meta"><div><span class="price">${price}$</span>${promo ? `<span class="old">${product.price_usd}$</span>` : ""}</div><span class="rating">${product.rating ? `★ ${product.rating} (${product.reviews_count})` : "جديد"}</span></div>
      <button class="buy">شراء عبر Telegram</button>
      <button class="cart">🛒 أضف للسلة</button>
      <button class="watch">🔔 مراقبة السعر والمخزون</button>
    `;
    card.querySelector(".buy").onclick = () => selectProduct(product);
    card.querySelector(".cart").onclick = () => addToCart(product);
    card.querySelector(".watch").onclick = () => watchProduct(product.id);
    root.appendChild(card);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

async function selectProduct(product) {
  if (!tg?.initData) { showMessage("افتح المتجر من داخل Telegram لإتمام الشراء.", true); return; }
  let target = "";
  let quantity = 1;
  if (product.requires_player_id || product.requires_link) {
    target = window.prompt(product.requires_player_id ? "أدخل Player ID" : "أدخل الرابط", "")?.trim() || "";
    if (!target) return;
  }
  if (product.requires_quantity) {
    quantity = Number(window.prompt(`الكمية من ${product.min_quantity} إلى ${product.max_quantity}`, String(product.min_quantity)));
    if (!Number.isInteger(quantity) || quantity < product.min_quantity || quantity > product.max_quantity) {
      showMessage("الكمية غير صالحة.", true); return;
    }
  }
  try {
    showMessage("⏳ جاري تنفيذ الطلب...");
    const result = await api("/checkout", { method: "POST", body: JSON.stringify({ product_id: product.id, target, quantity }) });
    if (result.delivery) {
      showMessage(`✅ تم التسليم: ${result.delivery}`);
    } else {
      showMessage(`✅ تم إنشاء الطلب #${result.order_id}. الحالة: ${result.status}`);
    }
    if (tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
  } catch (error) { showMessage(error.message, true); }
}

async function addToCart(product) {
  if (!tg?.initData) { showMessage("افتح المتجر من داخل Telegram لإضافة المنتجات.", true); return; }
  let target = "";
  let quantity = 1;
  if (product.requires_player_id || product.requires_link) {
    target = window.prompt(product.requires_player_id ? "أدخل Player ID" : "أدخل الرابط", "")?.trim() || "";
    if (!target) return;
  }
  if (product.requires_quantity) {
    quantity = Number(window.prompt(`الكمية من ${product.min_quantity} إلى ${product.max_quantity}`, String(product.min_quantity)));
    if (!Number.isInteger(quantity) || quantity < product.min_quantity || quantity > product.max_quantity) {
      showMessage("الكمية غير صالحة.", true); return;
    }
  }
  try {
    await api("/cart/items", { method: "POST", body: JSON.stringify({ product_id: product.id, target, quantity }) });
    showMessage("🛒 تمت إضافة المنتج إلى السلة.");
  } catch (error) { showMessage(error.message, true); }
}

async function renderCart() {
  if (!tg?.initData) { showMessage("افتح المتجر من داخل Telegram لعرض السلة.", true); return; }
  try {
    const items = await api("/cart");
    if (!items.length) { showMessage("🛒 السلة فارغة."); return; }
    showMessage(items.map(item => `${item.product_name} × ${item.quantity} = ${item.total_price_usd}$`).join("\\n"));
  } catch (error) { showMessage(error.message, true); }
}

async function checkoutCart() {
  if (!tg?.initData) { showMessage("افتح المتجر من داخل Telegram لإتمام السلة.", true); return; }
  const accepted = tg.showConfirm ? await new Promise(resolve => tg.showConfirm("سيتم تنفيذ عناصر السلة المتاحة. العناصر الفاشلة ستبقى للمحاولة لاحقاً.", resolve)) : window.confirm("تنفيذ السلة؟");
  if (!accepted) return;
  try {
    showMessage("⏳ جاري تنفيذ السلة...");
    const result = await api("/cart/checkout", { method: "POST" });
    const done = result.completed?.length || 0;
    const failed = result.failed?.length || 0;
    showMessage(`✅ اكتمل ${done} طلب${failed ? ` · تعذر تنفيذ ${failed}` : ""}.`);
  } catch (error) { showMessage(error.message, true); }
}

async function watchProduct(productId) {
  if (!tg?.initData) { showMessage("سجل دخولك من داخل Telegram لتفعيل التنبيه.", true); return; }
  try { await api("/watches", { method: "POST", body: JSON.stringify({ product_id: productId }) }); showMessage("🔔 تم تفعيل التنبيه لهذا المنتج."); }
  catch (error) { showMessage(error.message, true); }
}

async function load() {
  try {
    const catalog = await api("/catalog");
    state.categories = catalog.categories;
    state.products = flattenCatalog(catalog);
    renderChips(); renderProducts();
    if (tg?.initData) {
      const me = await api("/me");
      document.getElementById("balance").textContent = `${me.balance_usd}$`;
      document.getElementById("points").textContent = me.loyalty_points;
      document.getElementById("tier").textContent = me.loyalty_tier;
    } else showMessage("لإتمام الشراء، افتح المتجر من داخل Telegram.");
  } catch (error) { showMessage(error.message, true); }
}

document.getElementById("search").addEventListener("input", (event) => { state.query = event.target.value; renderProducts(); });
document.getElementById("cart-button").addEventListener("click", renderCart);
document.getElementById("checkout-cart").addEventListener("click", checkoutCart);
load();
