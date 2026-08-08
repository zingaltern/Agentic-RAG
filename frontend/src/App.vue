<script setup>
import ErrorBar from './components/ErrorBar.vue'
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">Agentic RAG</div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/">问答控制台</el-menu-item>
        <el-menu-item index="/documents">文档管理</el-menu-item>
        <el-menu-item index="/kbs">知识库管理</el-menu-item>
        <el-menu-item index="/settings">设置总览</el-menu-item>
      </el-menu>
      <div class="footer-note">旧测试页：<a href="/ui" target="_blank">/ui（降级入口）</a></div>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
    <ErrorBar />
  </el-container>
</template>
