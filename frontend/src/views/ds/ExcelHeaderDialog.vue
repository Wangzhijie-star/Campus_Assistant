<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus-secondary'
import { datasourceApi } from '@/api/datasource'

const emit = defineEmits(['preview'])
const visible = ref(false)
const loading = ref(false)
const sheets = ref<any[]>([])
let uploadId = ''
const init = (response: any) => {
  uploadId = response.uploadId
  sheets.value = response.sheetNames.map((sheetName: string) => ({
    sheetName, selected: true, headerMode: 'multi',
    headerStartRow: 1, headerEndRow: 2, dataStartRow: 3,
    dataEndRow: undefined, firstColumn: 1, lastColumn: undefined,
  }))
  visible.value = true
}
const convert = async () => {
  const selected = sheets.value.filter((s) => s.selected)
  if (!selected.length) return ElMessage.warning('请至少选择一个工作表')
  loading.value = true
  try {
    const response = await datasourceApi.convertExcel({ uploadId, sheets: selected })
    emit('preview', response)
    visible.value = false
  } finally {
    loading.value = false
  }
}
defineExpose({ init })
</script>

<template>
  <el-dialog v-model="visible" title="多级表头设置" width="min(1000px, 96vw)" :close-on-click-modal="false">
    <p>按工作表指定表头和数据范围，行列编号从 1 开始。结束位置留空时读取到有效数据末尾。</p>
    <el-alert title="请根据原文件填写范围；系统不会自动判断表头。数据区域存在合并单元格时需先整理原文件。" type="info" :closable="false" />
    <div style="max-height: 60vh; overflow: auto">
      <section v-for="sheet in sheets" :key="sheet.sheetName" style="padding: 16px 0; border-bottom: 1px solid #ddd">
        <el-checkbox v-model="sheet.selected">{{ sheet.sheetName }}</el-checkbox>
        <el-form v-if="sheet.selected" label-position="top" inline>
          <el-form-item label="表头模式">
            <el-select v-model="sheet.headerMode" style="width: 150px">
              <el-option label="多级表头" value="multi" />
              <el-option label="普通表头（首行）" value="single" />
            </el-select>
          </el-form-item>
          <template v-if="sheet.headerMode === 'multi'">
            <el-form-item label="表头起始行"><el-input-number v-model="sheet.headerStartRow" :min="1" :precision="0" /></el-form-item>
            <el-form-item label="表头结束行"><el-input-number v-model="sheet.headerEndRow" :min="1" :precision="0" /></el-form-item>
            <el-form-item label="数据起始行"><el-input-number v-model="sheet.dataStartRow" :min="2" :precision="0" /></el-form-item>
          </template>
          <el-form-item label="数据结束行（可选）"><el-input-number v-model="sheet.dataEndRow" :min="2" :precision="0" /></el-form-item>
          <el-form-item label="起始列"><el-input-number v-model="sheet.firstColumn" :min="1" :precision="0" /></el-form-item>
          <el-form-item label="结束列（可选）"><el-input-number v-model="sheet.lastColumn" :min="1" :precision="0" /></el-form-item>
        </el-form>
      </section>
    </div>
    <template #footer>
      <el-button :disabled="loading" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="loading" @click="convert">转换并预览</el-button>
    </template>
  </el-dialog>
</template>
