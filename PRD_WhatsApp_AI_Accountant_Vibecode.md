# Product Requirements Document (PRD)
**Project Name:** WhatsApp AI Accountant (B2B Micro-SaaS)
**Event/Initiative:** Vibecode @ Antigravity
**Date:** July 2026
**Document Status:** Draft / v1.0

---

## 1. Executive Summary
**WhatsApp AI Accountant** adalah B2B Micro-SaaS yang dirancang untuk Usaha Mikro, Kecil, dan Menengah (UMKM) di Indonesia. Sistem ini mengotomatiskan pencatatan keuangan bisnis (pemasukan & pengeluaran) melalui platform yang paling sering digunakan oleh UMKM: WhatsApp. Dengan memanfaatkan arsitektur Agentic AI, Multi-modal LLM, dan integrasi API resmi Meta, sistem secara cerdas mengekstrak data dari pesan teks kasual, pesan suara, maupun foto nota/struk, lalu menyinkronkannya langsung ke dashboard keuangan web.

## 2. Problem Statement
* **Friksi Pencatatan:** Pemilik UMKM di Indonesia sering mengabaikan pencatatan keuangan karena aplikasi akuntansi tradisional terlalu kompleks dan memakan waktu.
* **Format Data Tidak Terstruktur:** Transaksi sering kali terjadi secara *ad-hoc* (dicatat di kertas, difoto, atau sekadar diingat).
* **Literasi Digital Menengah:** UMKM membutuhkan antarmuka yang sudah familiar (WhatsApp) tanpa perlu mengunduh aplikasi baru atau mempelajari UI/UX yang rumit.

## 3. Target Audience & Roles
1. **Business Owner (Pemilik Usaha):** Mengelola dashboard utama, melihat analitik, mendaftarkan nomor karyawan, dan mencatat transaksi tingkat tinggi.
2. **Employee (Karyawan/Kasir):** Hanya memiliki akses untuk mengirim (mencatat) transaksi operasional harian via WhatsApp ke sistem bisnis, tanpa bisa melihat total omzet keseluruhan.

---

## 4. Key Features & Requirements (Epics)

### Epic 1: WhatsApp Ingestion & Multi-Tenant Routing
* **Feature:** Menerima input dari pengguna melalui Meta WhatsApp Business Cloud API.
* **Requirement:** 
  * Mendukung modalitas Teks, Gambar, dan Audio (Voice Notes).
  * Sistem identifikasi nomor pengirim (*Sender ID*) untuk memetakan pesan ke `tenant_id` dan `user_role` yang tepat secara otomatis.
  * *Fallback mechanism* jika pengguna tidak terdaftar (Onboarding flow).

### Epic 2: AI Multi-Agent & Extraction Pipeline
* **Feature:** Ekstraksi data keuangan (Nominal, Kategori, Tipe, Tanggal) dari input tidak terstruktur.
* **Requirement (Vibecode Strict Constraints):**
  * **DILARANG menggunakan Zero-shot API.**
  * **Text/Audio:** Rute ke Extraction Agent (menggunakan Tool Calling). Jika Audio, gunakan Auxiliary Model (Whisper STT) terlebih dahulu.
  * **Image (Struk/Nota):** Rute ke Vision Pipeline. Wajib menggunakan **NVIDIA Nemotron-OCR-v2** sebagai *pre-processor* untuk spatial-mapping, dilanjutkan ke Vision LLM (Gemini/GPT-4o) untuk *semantic reasoning* dan pemetaan ke skema JSON.

### Epic 3: Agentic Workflow & Tool Calling
* **Feature:** Interaksi dua arah yang cerdas antara AI dan pengguna.
* **Requirement:**
  * **Tool Calling:** AI diwajibkan memanggil fungsi `record_transaction()` ke database. 
  * **Missing Parameter Handling:** Jika pengguna mengirim "Bayar gaji kasir", AI tidak boleh berhalusinasi, melainkan memanggil fungsi `request_clarification("Berapa nominal gajinya, Kak?")`.
  * **Contextual RAG:** Injeksi *history* transaksi tenant (misal 50 transaksi terakhir) ke *prompt* agar AI paham taksonomi kategori unik milik tenant tersebut.

### Epic 4: Backend & Dashboard Synchronization
* **Feature:** Manajemen data terpusat dan tampilan UI.
* **Requirement:**
  * Database PostgreSQL dengan arsitektur Multi-tenant (Isolasi berdasarkan `tenant_id`).
  * Penyediaan REST API *endpoints* untuk ditarik oleh Web Dashboard (Next.js/React).
  * Arsitektur *Asynchronous*: Webhook WhatsApp merespons 200 OK secara instan, pemrosesan AI berjalan di *background*, dan notifikasi sukses dikirim secara proaktif via API Meta.

---

## 5. Technical Architecture Overview

1. **Frontend:** WhatsApp UI (User), Web Dashboard (Owner).
2. **Gateway:** Meta WA Cloud API Webhook.
3. **Core API / Backend:** Python (FastAPI / LangGraph) atau Node.js.
4. **AI Models & Pipeline:**
   * **Router:** LLM Supervisor Agent.
   * **Vision Stack:** NVIDIA Nemotron-OCR-v2 (Auxiliary) + Vision LLM.
   * **Memory:** Vector DB ringan (RAG) & Session Memory.
5. **Database:** PostgreSQL (Multi-tenant schema).

---

## 6. Success Metrics (KPIs)
* **Accuracy:** > 90% keberhasilan ekstraksi entitas (Nominal & Kategori) dari nota gambar yang pudar atau tulisan tangan.
* **Latency:** Waktu respons dari pengguna mengirim pesan hingga mendapat balasan konfirmasi < 10 detik.
* **User Engagement:** Jumlah transaksi tercatat per *tenant* per minggu aktif.
* **Compliance:** 100% menggunakan Tool Calling & RAG (Memenuhi syarat penjurian Vibecode Antigravity).

---


