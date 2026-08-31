/* ==========================================================================
   community.js — Chăm Culture Community client
   --------------------------------------------------------------------------
   Talks to the unified backend (same origin):
     GET  /api/auth/me
     GET  /api/community/posts?category=&sort=&page=&limit=
     POST /api/community/posts                (multipart: content, category, images[])
     POST /api/community/posts/{id}/like
     GET  /api/community/posts/{id}/comments
     POST /api/community/posts/{id}/comments  ({content})
     POST /api/community/posts/{id}/share     ({content})
     GET  /api/community/stats/topics
     GET  /api/community/stats/active-members
     GET  /api/community/profiles/{id}?page=&limit=
   Auth source of truth is the HTTPOnly session cookie, never localStorage.
   ========================================================================== */

(() => {
  "use strict";

  const API = "/api/community";
  const LOGIN_PAGE = "/index.html";
  const DEFAULT_AVATAR = "/avatarmacdinh.svg";
  const PAGE_LIMIT = 20;

  const $ = (id) => document.getElementById(id);

  const dom = {
    authLoading: $("authLoadingScreen"),
    appShell: $("appShell"),
    navUserAvatar: $("navUserAvatar"),
    navUserName: $("navUserName"),
    feedTabs: $("feedTabs"),
    feedState: $("feedState"),
    postsContainer: $("postsContainer"),
    topicList: $("topicListContainer"),
    openMyProfileBtn: $("openMyProfileBtn"),
    openPostModalBtn: $("openPostModalBtn"),
    // create post modal
    postModal: $("postModal"),
    postForm: $("postForm"),
    postContentInput: $("postContentInput"),
    postCharCount: $("postCharCount"),
    fileInput: $("fileInput"),
    dragZone: $("dragZone"),
    previewSlots: $("previewSlotsContainer"),
    selectedImageCount: $("selectedImageCount"),
    topicSelect: $("topicSelect"),
    submitPostBtn: $("submitPostBtn"),
    modalUserAvatar: $("modalUserAvatar"),
    modalUserName: $("modalUserName"),
    // share modal
    shareModal: $("shareModal"),
    shareForm: $("shareForm"),
    shareContentInput: $("shareContentInput"),
    shareCharCount: $("shareCharCount"),
    shareOriginalPreview: $("shareOriginalPreview"),
    submitShareBtn: $("submitShareBtn"),
    shareModalUserAvatar: $("shareModalUserAvatar"),
    shareModalUserName: $("shareModalUserName"),
    // profile view
    profileView: $("profileView"),
    profileAvatar: $("profileAvatar"),
    directEditAvatarBtn: $("directEditAvatarBtn"),
    directAvatarInput: $("directAvatarInput"),
    profileViewTitle: $("profileViewTitle"),
    profileEmail: $("profileEmail"),
    profilePostCount: $("profilePostCount"),
    profileFollowersCount: $("profileFollowersCount"),
    profileFollowingCount: $("profileFollowingCount"),
    profileState: $("profileState"),
    profilePostsContainer: $("profilePostsContainer"),
    backToCommunityBtn: $("backToCommunityBtn"),
    editProfileBtn: $("editProfileBtn"),
    followUserBtn: $("followUserBtn"),
    // edit profile modal
    editProfileModal: $("editProfileModal"),
    editProfileForm: $("editProfileForm"),
    editAvatarInput: $("editAvatarInput"),
    editAvatarPreview: $("editAvatarPreview"),
    editUsernameInput: $("editUsernameInput"),
    editProfileStatus: $("editProfileStatus"),
    editProfileSubmitBtn: $("editProfileSubmitBtn"),
    // search
    searchToggleBtn: $("searchToggleBtn"),
    searchBar: $("searchBar"),
    postSearchInput: $("postSearchInput"),
    clearSearchBtn: $("clearSearchBtn"),
    toastContainer: $("toastContainer"),
  };

  const state = {
    currentUser: null,
    filter: { category: "", sort: "latest" },
    posts: [],
    selectedFiles: [],
    shareTargetId: null,
    loading: false,
  };

  // ----------------------------------------------------------------------- //
  // Utilities
  // ----------------------------------------------------------------------- //
  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");

  const avatarOf = (url) => (url && String(url).trim() ? url : DEFAULT_AVATAR);

  function relativeTime(iso) {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return "";
    const diff = Math.max(0, Date.now() - then);
    const min = Math.floor(diff / 60000);
    if (min < 1) return "Vừa xong";
    if (min < 60) return `${min} phút trước`;
    const hours = Math.floor(min / 60);
    if (hours < 24) return `${hours} giờ trước`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days} ngày trước`;
    return new Date(iso).toLocaleDateString("vi-VN");
  }

  function toast(message, type = "info") {
    if (!dom.toastContainer) return;
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = message;
    dom.toastContainer.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  async function apiFetch(url, options = {}) {
    const res = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });
    let body = null;
    const text = await res.text();
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = { message: text };
      }
    }
    if (!res.ok) {
      const err = new Error((body && body.message) || `HTTP ${res.status}`);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  function stateCard(icon, title, text) {
    return `
      <div class="state-card">
        <i class="fa-solid ${icon}"></i>
        <strong>${escapeHtml(title)}</strong>
        <p>${escapeHtml(text)}</p>
      </div>`;
  }

  function imagesGrid(urls, opts = {}) {
    if (!Array.isArray(urls) || urls.length === 0) return "";
    const cls = opts.embedded ? "embedded-images-grid" : "post-images-grid";
    const itemCls = opts.embedded ? "embedded-img-item" : "post-img-item";
    const count = Math.min(urls.length, 4);
    const imgs = urls
      .slice(0, 4)
      .map((u) => `<img src="${escapeHtml(u)}" alt="Hình ảnh" class="${itemCls}" loading="lazy">`)
      .join("");
    return `<div class="${cls}" data-count="${count}">${imgs}</div>`;
  }

  // ----------------------------------------------------------------------- //
  // Auth gate
  // ----------------------------------------------------------------------- //
  async function ensureAuthenticated() {
    try {
      const data = await apiFetch("/api/auth/me");
      state.currentUser = data.user;
      return true;
    } catch (err) {
      window.location.href = LOGIN_PAGE;
      return false;
    }
  }

  function renderIdentity() {
    const u = state.currentUser;
    if (!u) return;
    const av = avatarOf(u.avatar_url);
    if (dom.navUserAvatar) {
      dom.navUserAvatar.src = av;
      dom.navUserAvatar.alt = u.username;
    }
    if (dom.navUserName) dom.navUserName.textContent = u.username;
    if (dom.modalUserAvatar) dom.modalUserAvatar.src = av;
    if (dom.modalUserName) dom.modalUserName.textContent = u.username;
    if (dom.shareModalUserAvatar) dom.shareModalUserAvatar.src = av;
    if (dom.shareModalUserName) dom.shareModalUserName.textContent = u.username;
  }

  // ----------------------------------------------------------------------- //
  // Feed
  // ----------------------------------------------------------------------- //
  async function loadFeed() {
    if (!dom.postsContainer) return;
    state.loading = true;
    dom.postsContainer.innerHTML = stateCard("fa-spinner fa-spin", "Đang tải bài viết", "Vui lòng chờ trong giây lát.");
    const params = new URLSearchParams({ sort: state.filter.sort, page: "1", limit: String(PAGE_LIMIT) });
    if (state.filter.category) params.set("category", state.filter.category);
    try {
      const data = await apiFetch(`${API}/posts?${params.toString()}`);
      state.posts = data.items || [];
      renderPosts(state.posts);
    } catch (err) {
      dom.postsContainer.innerHTML = stateCard("fa-triangle-exclamation", "Không tải được bài viết", err.message);
    } finally {
      state.loading = false;
    }
  }

  function renderPosts(posts) {
    if (!dom.postsContainer) return;
    if (!posts.length) {
      dom.postsContainer.innerHTML = stateCard("fa-feather", "Chưa có bài viết", "Hãy là người đầu tiên chia sẻ trong mục này.");
      return;
    }
    dom.postsContainer.innerHTML = posts.map(postCardHtml).join("");
  }

  function embeddedHtml(post) {
    if (!post.shared_post_id) return "";
    const orig = post.original_post;
    if (!orig) {
      return `<div class="embedded-post-deleted">Bài viết gốc đã bị xóa hoặc không còn khả dụng.</div>`;
    }
    return `
      <div class="embedded-post-box">
        <div class="embedded-header">
          <img class="embedded-avatar author-link" data-user-id="${escapeHtml(orig.author && orig.author.id)}" src="${escapeHtml(avatarOf(orig.author && orig.author.avatar_url))}" alt="" loading="lazy">
          <div>
            <span class="embedded-name author-link" data-user-id="${escapeHtml(orig.author && orig.author.id)}">${escapeHtml(orig.author ? orig.author.username : "Người dùng")}</span>
            <span class="embedded-time">${escapeHtml(relativeTime(orig.created_at))}</span>
          </div>
        </div>
        ${orig.content ? `<p class="embedded-content">${escapeHtml(orig.content)}</p>` : ""}
        ${imagesGrid(orig.image_urls, { embedded: true })}
      </div>`;
  }

  function postCardHtml(post) {
    const author = post.author || { username: "Người dùng", avatar_url: null, id: "" };
    const liked = post.liked_by_current_user ? " is-liked" : "";
    return `
      <article class="post-card" data-post-id="${escapeHtml(post.id)}">
        <div class="post-main">
          <div class="post-user-info">
            <div class="user-meta">
              <img src="${escapeHtml(avatarOf(author.avatar_url))}" alt="${escapeHtml(author.username)}"
                   class="user-avatar author-link" data-user-id="${escapeHtml(author.id)}" loading="lazy">
              <div>
                <span class="user-name author-link" data-user-id="${escapeHtml(author.id)}">${escapeHtml(author.username)}</span>
                <div class="post-time-tag">
                  <span>${escapeHtml(relativeTime(post.created_at))}</span>
                  <span class="post-tag-badge">${escapeHtml(post.category)}</span>
                </div>
              </div>
            </div>
          </div>
          ${post.content ? `<p class="post-text">${escapeHtml(post.content)}</p>` : ""}
          ${imagesGrid(post.image_urls)}
          ${embeddedHtml(post)}
          <div class="post-footer">
            <button type="button" class="action-item like-button${liked}" data-action="like">
              <i class="fa-solid fa-heart"></i> <span class="like-count">${post.like_count}</span>
            </button>
            <button type="button" class="action-item" data-action="comments">
              <i class="fa-regular fa-comment"></i> <span class="comment-count">${post.comment_count}</span> bình luận
            </button>
            <button type="button" class="action-item" data-action="share">
              <i class="fa-regular fa-share-from-square"></i> <span>Chia sẻ</span>
            </button>
          </div>
        </div>
        <div class="comments-panel is-hidden" data-comments-for="${escapeHtml(post.id)}"></div>
      </article>`;
  }

  // ----------------------------------------------------------------------- //
  // Post interactions (event delegation)
  // ----------------------------------------------------------------------- //
  async function handleFeedClick(event) {
    const authorLink = event.target.closest(".author-link");
    if (authorLink && authorLink.dataset.userId) {
      openProfile(authorLink.dataset.userId);
      return;
    }
    const btn = event.target.closest("[data-action]");
    if (!btn) return;
    const card = btn.closest(".post-card");
    if (!card) return;
    const postId = card.dataset.postId;
    const action = btn.dataset.action;
    if (action === "like") return toggleLike(postId, btn);
    if (action === "comments") return toggleComments(postId, card);
    if (action === "share") return openShareModal(postId);
  }

  async function toggleLike(postId, btn) {
    btn.disabled = true;
    try {
      const data = await apiFetch(`${API}/posts/${postId}/like`, { method: "POST" });
      btn.classList.toggle("is-liked", data.liked);
      const countEl = btn.querySelector(".like-count");
      if (countEl) countEl.textContent = data.like_count;
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  async function toggleComments(postId, card) {
    const panel = card.querySelector(".comments-panel");
    if (!panel) return;
    const isHidden = panel.classList.contains("is-hidden");
    if (!isHidden) {
      panel.classList.add("is-hidden");
      return;
    }
    panel.classList.remove("is-hidden");
    if (panel.dataset.loaded === "1") return;
    panel.innerHTML = `<div class="comments-list"><p class="comment-time"><span class="inline-spinner"></span>Đang tải bình luận...</p></div>`;
    try {
      const data = await apiFetch(`${API}/posts/${postId}/comments`);
      renderCommentsPanel(panel, postId, data.items || []);
      panel.dataset.loaded = "1";
    } catch (err) {
      panel.innerHTML = `<div class="comments-list">${stateCard("fa-triangle-exclamation", "Lỗi", err.message)}</div>`;
    }
  }

  function commentItemHtml(c) {
    const u = c.user || { username: "Người dùng", avatar_url: null };
    return `
      <div class="comment-item">
        <img class="comment-avatar" src="${escapeHtml(avatarOf(u.avatar_url))}" alt="">
        <div class="comment-bubble">
          <span class="comment-author">${escapeHtml(u.username)}</span>
          <p class="comment-content">${escapeHtml(c.content)}</p>
          <div class="comment-time">${escapeHtml(relativeTime(c.created_at))}</div>
        </div>
      </div>`;
  }

  function renderCommentsPanel(panel, postId, comments) {
    panel.innerHTML = `
      <div class="comments-list">
        ${comments.length ? comments.map(commentItemHtml).join("") : '<p class="comment-time">Chưa có bình luận nào.</p>'}
      </div>
      <form class="comment-form">
        <textarea class="comment-input" placeholder="Viết bình luận..." maxlength="1000" required></textarea>
        <button type="submit" class="comment-submit-btn"><i class="fa-solid fa-paper-plane"></i></button>
      </form>`;
    const form = panel.querySelector(".comment-form");
    form.addEventListener("submit", (e) => submitComment(e, postId, panel));
  }

  async function submitComment(event, postId, panel) {
    event.preventDefault();
    const input = panel.querySelector(".comment-input");
    const btn = panel.querySelector(".comment-submit-btn");
    const content = input.value.trim();
    if (!content) return;
    btn.disabled = true;
    try {
      const comment = await apiFetch(`${API}/posts/${postId}/comments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const list = panel.querySelector(".comments-list");
      const placeholder = list.querySelector("p.comment-time");
      if (placeholder) placeholder.remove();
      list.insertAdjacentHTML("beforeend", commentItemHtml(comment));
      input.value = "";
      bumpCounter(postId, ".comment-count", +1);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  function bumpCounter(postId, selector, delta) {
    const card = dom.postsContainer.querySelector(`.post-card[data-post-id="${CSS.escape(postId)}"]`);
    if (!card) return;
    const el = card.querySelector(selector);
    if (el) el.textContent = String((parseInt(el.textContent, 10) || 0) + delta);
  }

  // ----------------------------------------------------------------------- //
  // Modals (generic show/hide)
  // ----------------------------------------------------------------------- //
  function openModal(modal) {
    if (modal) modal.classList.add("show");
  }
  function closeModal(modal) {
    if (modal) modal.classList.remove("show");
  }

  // ----------------------------------------------------------------------- //
  // Create post
  // ----------------------------------------------------------------------- //
  function resetCreateForm() {
    if (dom.postForm) dom.postForm.reset();
    state.selectedFiles = [];
    if (dom.fileInput) dom.fileInput.value = "";
    renderPreviews();
    if (dom.postCharCount) dom.postCharCount.textContent = "0";
  }

  function renderPreviews() {
    if (!dom.previewSlots) return;
    dom.previewSlots.innerHTML = state.selectedFiles
      .map(
        (f, i) => `
        <div class="preview-slot">
          <img src="${URL.createObjectURL(f)}" alt="preview">
          <button type="button" class="remove-preview-btn" data-remove="${i}">&times;</button>
        </div>`
      )
      .join("");
    if (dom.selectedImageCount) dom.selectedImageCount.textContent = `${state.selectedFiles.length}/4`;
  }

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    for (const f of incoming) {
      if (state.selectedFiles.length >= 4) {
        toast("Chỉ được tối đa 4 ảnh.", "info");
        break;
      }
      if (!/^image\/(png|jpeg|webp)$/.test(f.type)) {
        toast(`Bỏ qua ${f.name}: chỉ nhận PNG/JPG/WebP.`, "info");
        continue;
      }
      state.selectedFiles.push(f);
    }
    renderPreviews();
  }

  async function submitPost(event) {
    event.preventDefault();
    const content = dom.postContentInput ? dom.postContentInput.value.trim() : "";
    const category = dom.topicSelect ? dom.topicSelect.value : "";
    
    // Clear previous error if any
    if (dom.topicSelect) {
      dom.topicSelect.classList.remove("input-error");
    }

    if (!category) {
      toast("Vui lòng chọn một chuyên mục cho bài viết.", "error");
      if (dom.topicSelect) {
        dom.topicSelect.classList.add("input-error");
        dom.topicSelect.focus();
      }
      return;
    }

    if (!content && state.selectedFiles.length === 0) {
      toast("Vui lòng nhập nội dung hoặc chọn ảnh.", "info");
      return;
    }
    const fd = new FormData();
    fd.append("content", content);
    fd.append("category", category);
    state.selectedFiles.forEach((f) => fd.append("images", f));

    dom.submitPostBtn.disabled = true;
    const original = dom.submitPostBtn.innerHTML;
    dom.submitPostBtn.innerHTML = '<span class="inline-spinner"></span> Đang đăng...';
    try {
      await apiFetch(`${API}/posts`, { method: "POST", body: fd });
      closeModal(dom.postModal);
      resetCreateForm();
      toast("Đăng bài thành công!", "success");
      await Promise.all([loadFeed(), loadSidebar()]);
    } catch (err) {
      if (err.body && err.body.error === "CATEGORY_REQUIRED") {
        toast(err.body.message || "Vui lòng chọn một chuyên mục cho bài viết.", "error");
        if (dom.topicSelect) {
          dom.topicSelect.classList.add("input-error");
          dom.topicSelect.focus();
        }
      } else {
        toast(err.message || "Không thể đăng bài.", "error");
      }
    } finally {
      dom.submitPostBtn.disabled = false;
      dom.submitPostBtn.innerHTML = original;
    }
  }

  // ----------------------------------------------------------------------- //
  // Share
  // ----------------------------------------------------------------------- //
  function openShareModal(postId) {
    const post = state.posts.find((p) => p.id === postId);
    state.shareTargetId = postId;
    if (dom.shareContentInput) dom.shareContentInput.value = "";
    if (dom.shareCharCount) dom.shareCharCount.textContent = "0";
    if (dom.shareOriginalPreview && post) {
      dom.shareOriginalPreview.innerHTML = `
        <div class="embedded-post-box">
          <div class="embedded-header">
            <img class="embedded-avatar author-link" data-user-id="${escapeHtml(post.author && post.author.id)}" src="${escapeHtml(avatarOf(post.author && post.author.avatar_url))}" alt="" loading="lazy">
            <div>
              <span class="embedded-name author-link" data-user-id="${escapeHtml(post.author && post.author.id)}">${escapeHtml(post.author ? post.author.username : "Người dùng")}</span>
              <span class="embedded-time">${escapeHtml(relativeTime(post.created_at))}</span>
            </div>
          </div>
          ${post.content ? `<p class="embedded-content">${escapeHtml(post.content)}</p>` : ""}
          ${imagesGrid(post.image_urls, { embedded: true })}
        </div>`;
    }
    openModal(dom.shareModal);
  }

  async function submitShare(event) {
    event.preventDefault();
    if (!state.shareTargetId) return;
    const content = dom.shareContentInput ? dom.shareContentInput.value.trim() : "";
    dom.submitShareBtn.disabled = true;
    try {
      await apiFetch(`${API}/posts/${state.shareTargetId}/share`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      closeModal(dom.shareModal);
      toast("Đã chia sẻ bài viết!", "success");
      await Promise.all([loadFeed(), loadSidebar()]);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      dom.submitShareBtn.disabled = false;
      state.shareTargetId = null;
    }
  }

  // ----------------------------------------------------------------------- //
  // Sidebar
  // ----------------------------------------------------------------------- //
  async function loadSidebar() {
    await loadTopics();
  }

  async function loadTopics() {
    if (!dom.topicList) return;
    try {
      const data = await apiFetch(`${API}/stats/topics`);
      const items = data.items || [];
      dom.topicList.innerHTML = items.length
        ? items
            .map(
              (t) => `
          <div class="topic-item">
            <span class="topic-name"># ${escapeHtml(t.category)}</span>
            <span class="topic-count">${t.post_count} bài viết</span>
          </div>`
            )
            .join("")
        : '<p class="topic-count">Chưa có chủ đề nào.</p>';
    } catch {
      dom.topicList.innerHTML = '<p class="topic-count">Không tải được chủ đề.</p>';
    }
  }



  // ----------------------------------------------------------------------- //
  // Profile view
  // ----------------------------------------------------------------------- //
  function showProfileView(show) {
    if (!dom.appShell || !dom.profileView) return;
    dom.appShell.classList.toggle("profile-active", show);
    dom.profileView.classList.toggle("is-hidden", !show);
  }

  async function openProfile(userId, push = true) {
    if (!userId) return;
    showProfileView(true);
    if (push) {
      const url = `${window.location.pathname}?profile=${encodeURIComponent(userId)}`;
      window.history.pushState({ profile: userId }, "", url);
    }
    dom.profilePostsContainer.innerHTML = "";
    dom.profileState.innerHTML = stateCard("fa-spinner fa-spin", "Đang tải hồ sơ", "Vui lòng chờ.");
    dom.profileViewTitle.textContent = "Đang tải...";
    dom.profileEmail.classList.add("is-hidden");

    try {
      let page = 1;
      let hasNext = true;
      let allItems = [];
      let profile = null;
      while (hasNext) {
        const data = await apiFetch(`${API}/profiles/${encodeURIComponent(userId)}?page=${page}&limit=50`);
        profile = data;
        allItems = allItems.concat(data.items || []);
        hasNext = data.pagination && data.pagination.has_next;
        page += 1;
        if (page > 50) break;
      }
      const u = profile.user || {};
      dom.profileAvatar.src = avatarOf(u.avatar_url);
      dom.profileViewTitle.textContent = u.username || "Người dùng";
      if (u.email) {
        dom.profileEmail.textContent = u.email;
        dom.profileEmail.classList.remove("is-hidden");
      } else {
        dom.profileEmail.classList.add("is-hidden");
      }
      if (state.currentUser && String(userId) === String(state.currentUser.id)) {
        dom.editProfileBtn.classList.remove("is-hidden");
        if (dom.directEditAvatarBtn) dom.directEditAvatarBtn.classList.remove("is-hidden");
        dom.editUsernameInput.value = u.username || "";
        if (dom.followUserBtn) dom.followUserBtn.classList.add("is-hidden");
      } else {
        dom.editProfileBtn.classList.add("is-hidden");
        if (dom.directEditAvatarBtn) dom.directEditAvatarBtn.classList.add("is-hidden");
        if (dom.followUserBtn) {
          dom.followUserBtn.classList.remove("is-hidden");
          dom.followUserBtn.dataset.userId = u.id;
          if (profile.is_following) {
            dom.followUserBtn.textContent = "Đang theo dõi";
            dom.followUserBtn.classList.remove("btn-primary");
            dom.followUserBtn.classList.add("btn-secondary");
          } else {
            dom.followUserBtn.textContent = "Theo dõi";
            dom.followUserBtn.classList.remove("btn-secondary");
            dom.followUserBtn.classList.add("btn-primary");
          }
        }
      }
      if (dom.profilePostCount) dom.profilePostCount.textContent = String(profile.post_count || 0);
      if (dom.profileFollowersCount) dom.profileFollowersCount.textContent = String(profile.followers_count || 0);
      if (dom.profileFollowingCount) dom.profileFollowingCount.textContent = String(profile.following_count || 0);
      dom.profileState.innerHTML = "";
      dom.profilePostsContainer.innerHTML = allItems.length
        ? allItems.map(postCardHtml).join("")
        : stateCard("fa-feather", "Chưa có bài viết", "Người dùng này chưa đăng bài nào.");
    } catch (err) {
      dom.profileState.innerHTML = stateCard("fa-triangle-exclamation", "Không tải được hồ sơ", err.message);
    }
  }

  async function submitEditProfile(event) {
    event.preventDefault();
    const fd = new FormData(dom.editProfileForm);
    
    dom.editProfileSubmitBtn.disabled = true;
    const original = dom.editProfileSubmitBtn.innerHTML;
    dom.editProfileSubmitBtn.innerHTML = '<span class="inline-spinner"></span> Đang lưu...';
    dom.editProfileStatus.textContent = "";
    dom.editProfileStatus.style.color = "blue";
    
    try {
      const res = await apiFetch(`/api/auth/me`, { method: "PATCH", body: fd });
      if (res.user) {
        state.currentUser = res.user;
        renderIdentity();
        openProfile(state.currentUser.id, false);
      }
      closeModal(dom.editProfileModal);
      toast("Cập nhật hồ sơ thành công!", "success");
    } catch (err) {
      dom.editProfileStatus.style.color = "red";
      dom.editProfileStatus.textContent = err.message || "Đã xảy ra lỗi khi lưu thông tin.";
    } finally {
      dom.editProfileSubmitBtn.disabled = false;
      dom.editProfileSubmitBtn.innerHTML = original;
    }
  }

  async function handleDirectAvatarUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    if (!file.type.startsWith("image/")) {
      toast("Vui lòng chọn file hình ảnh.", "error");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      toast("Kích thước ảnh tối đa là 5MB.", "error");
      return;
    }

    const fd = new FormData();
    fd.append("avatar", file);

    const prevSrc = dom.profileAvatar.src;
    dom.profileAvatar.src = "/avatarmacdinh.svg"; // Tạm thời hiện loading/mặc định

    try {
      const res = await apiFetch(`/api/auth/me`, { method: "PATCH", body: fd });
      if (res.user) {
        state.currentUser = res.user;
        renderIdentity();
        if (state.currentUser.id) {
            openProfile(state.currentUser.id, false);
        }
        toast("Cập nhật ảnh đại diện thành công!", "success");
      }
    } catch (err) {
      dom.profileAvatar.src = prevSrc;
      toast(err.message || "Lỗi khi tải ảnh lên.", "error");
    } finally {
      event.target.value = ""; // Xóa input để lần sau chọn lại file cùng tên vẫn chạy onchange
    }
  }

  async function toggleFollow(userId) {
    if (!state.currentUser) {
      toast("Vui lòng đăng nhập để theo dõi.", "error");
      return;
    }

    const btn = dom.followUserBtn;
    if (!btn) return;
    
    // Optimistic Update
    const isCurrentlyFollowing = btn.classList.contains("btn-secondary");
    btn.disabled = true;
    
    let currentFollowers = 0;
    if (dom.profileFollowersCount) {
        currentFollowers = parseInt(dom.profileFollowersCount.textContent, 10) || 0;
    }

    if (isCurrentlyFollowing) {
      btn.textContent = "Theo dõi";
      btn.classList.remove("btn-secondary");
      btn.classList.add("btn-primary");
      if (dom.profileFollowersCount) dom.profileFollowersCount.textContent = String(Math.max(0, currentFollowers - 1));
    } else {
      btn.textContent = "Đang theo dõi";
      btn.classList.remove("btn-primary");
      btn.classList.add("btn-secondary");
      if (dom.profileFollowersCount) dom.profileFollowersCount.textContent = String(currentFollowers + 1);
    }

    try {
      const data = await apiFetch(`${API}/profiles/${encodeURIComponent(userId)}/follow`, { method: "POST" });
      // Cập nhật lại UI chính xác theo server trả về (phòng hờ)
      if (data.following) {
        btn.textContent = "Đang theo dõi";
        btn.classList.remove("btn-primary");
        btn.classList.add("btn-secondary");
      } else {
        btn.textContent = "Theo dõi";
        btn.classList.remove("btn-secondary");
        btn.classList.add("btn-primary");
      }
    } catch (err) {
      // Revert if failed
      toast(err.message || "Không thể thực hiện hành động này.", "error");
      if (isCurrentlyFollowing) {
        btn.textContent = "Đang theo dõi";
        btn.classList.remove("btn-primary");
        btn.classList.add("btn-secondary");
        if (dom.profileFollowersCount) dom.profileFollowersCount.textContent = String(currentFollowers); // revert
      } else {
        btn.textContent = "Theo dõi";
        btn.classList.remove("btn-secondary");
        btn.classList.add("btn-primary");
        if (dom.profileFollowersCount) dom.profileFollowersCount.textContent = String(currentFollowers); // revert
      }
    } finally {
      btn.disabled = false;
    }
  }

  function closeProfileView(push = true) {
    showProfileView(false);
    if (push) window.history.pushState({}, "", window.location.pathname);
  }

  // ----------------------------------------------------------------------- //
  // Search (client-side filter of loaded posts)
  // ----------------------------------------------------------------------- //
  function applySearch(term) {
    const q = term.trim().toLowerCase();
    const cards = dom.postsContainer.querySelectorAll(".post-card");
    cards.forEach((card) => {
      const text = card.textContent.toLowerCase();
      card.style.display = !q || text.includes(q) ? "" : "none";
    });
  }

  // ----------------------------------------------------------------------- //
  // Wiring
  // ----------------------------------------------------------------------- //
  function bindEvents() {
    if (dom.postsContainer) dom.postsContainer.addEventListener("click", handleFeedClick);
    if (dom.profilePostsContainer) dom.profilePostsContainer.addEventListener("click", handleFeedClick);

    // Tabs
    if (dom.feedTabs) {
      dom.feedTabs.addEventListener("click", (e) => {
        const tab = e.target.closest(".tab-item");
        if (!tab) return;
        dom.feedTabs.querySelectorAll(".tab-item").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        state.filter.category = tab.dataset.category || "";
        state.filter.sort = tab.dataset.sort || "latest";
        loadFeed();
      });
    }

    // Create post modal
    if (dom.openPostModalBtn) dom.openPostModalBtn.addEventListener("click", () => openModal(dom.postModal));
    if (dom.postForm) dom.postForm.addEventListener("submit", submitPost);
    if (dom.topicSelect) {
      dom.topicSelect.addEventListener("change", () => {
        dom.topicSelect.classList.remove("input-error");
      });
    }
    if (dom.postContentInput && dom.postCharCount) {
      dom.postContentInput.addEventListener("input", () => {
        dom.postCharCount.textContent = String(dom.postContentInput.value.length);
      });
    }
    if (dom.dragZone && dom.fileInput) {
      dom.dragZone.addEventListener("click", () => dom.fileInput.click());
      dom.dragZone.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); dom.fileInput.click(); }
      });
      dom.dragZone.addEventListener("dragover", (e) => { e.preventDefault(); dom.dragZone.classList.add("is-dragging"); });
      dom.dragZone.addEventListener("dragleave", () => dom.dragZone.classList.remove("is-dragging"));
      dom.dragZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dom.dragZone.classList.remove("is-dragging");
        addFiles(e.dataTransfer.files);
      });
      dom.fileInput.addEventListener("change", (e) => addFiles(e.target.files));
    }
    if (dom.previewSlots) {
      dom.previewSlots.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-remove]");
        if (!btn) return;
        state.selectedFiles.splice(parseInt(btn.dataset.remove, 10), 1);
        renderPreviews();
      });
    }

    // Share modal
    if (dom.shareForm) dom.shareForm.addEventListener("submit", submitShare);
    if (dom.shareContentInput && dom.shareCharCount) {
      dom.shareContentInput.addEventListener("input", () => {
        dom.shareCharCount.textContent = String(dom.shareContentInput.value.length);
      });
    }

    // Generic modal close buttons
    document.querySelectorAll("[data-close-modal]").forEach((btn) => {
      btn.addEventListener("click", () => closeModal($(btn.dataset.closeModal)));
    });
    [dom.postModal, dom.shareModal].forEach((modal) => {
      if (!modal) return;
      modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(modal); });
    });

    // Profile
    if (dom.openMyProfileBtn) {
      dom.openMyProfileBtn.addEventListener("click", () => {
        if (state.currentUser) openProfile(state.currentUser.id);
      });
    }
    if (dom.backToCommunityBtn) dom.backToCommunityBtn.addEventListener("click", () => closeProfileView());

    if (dom.editProfileBtn) {
      dom.editProfileBtn.addEventListener("click", () => {
        openModal(dom.editProfileModal);
      });
    }
    
    if (dom.followUserBtn) {
      dom.followUserBtn.addEventListener("click", (e) => {
        const uid = e.target.dataset.userId;
        if (uid) toggleFollow(uid);
      });
    }
    if (dom.editProfileForm) dom.editProfileForm.addEventListener("submit", submitEditProfile);
    if (dom.editAvatarInput && dom.editAvatarPreview) {
      dom.editAvatarInput.addEventListener("change", (e) => {
        const file = e.target.files[0];
        if (file) {
          const reader = new FileReader();
          reader.onload = (e) => { dom.editAvatarPreview.src = e.target.result; };
          reader.readAsDataURL(file);
        }
      });
    }

    if (dom.directEditAvatarBtn && dom.directAvatarInput) {
      dom.directEditAvatarBtn.addEventListener("click", () => dom.directAvatarInput.click());
      dom.directAvatarInput.addEventListener("change", handleDirectAvatarUpload);
    }

    window.addEventListener("popstate", () => {
      const pid = new URLSearchParams(window.location.search).get("profile");
      if (pid) openProfile(pid, false);
      else closeProfileView(false);
    });

    // Search
    if (dom.searchToggleBtn && dom.searchBar) {
      dom.searchToggleBtn.addEventListener("click", () => {
        dom.searchBar.classList.toggle("is-hidden");
        if (!dom.searchBar.classList.contains("is-hidden") && dom.postSearchInput) dom.postSearchInput.focus();
      });
    }
    if (dom.postSearchInput) dom.postSearchInput.addEventListener("input", (e) => applySearch(e.target.value));
    if (dom.clearSearchBtn && dom.postSearchInput) {
      dom.clearSearchBtn.addEventListener("click", () => {
        dom.postSearchInput.value = "";
        applySearch("");
      });
    }
  }

  async function init() {
    const ok = await ensureAuthenticated();
    if (!ok) return;

    if (dom.authLoading) dom.authLoading.classList.add("is-hidden");
    if (dom.appShell) dom.appShell.classList.remove("is-hidden");

    renderIdentity();
    bindEvents();
    await Promise.all([loadFeed(), loadSidebar()]);

    const pid = new URLSearchParams(window.location.search).get("profile");
    if (pid) openProfile(pid, false);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
