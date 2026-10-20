/**
 * Element Plus 按需装配（零新增依赖）
 * --------------------------------------------------------------------------
 * 直接 import 'element-plus' 会把整套组件与 360KB 全量 CSS 打进首包（约 +258KB gzip）。
 * 这里改为「深路径引入组件 + 组件级样式」：只打包实际用到的 20 个组件，
 * 并且样式也只含这些组件的 theme-chalk（各 style/css.mjs 自带 popper/scrollbar 等依赖样式）。
 *
 * 约定：模板里新增 <el-xxx> 时，必须在此登记组件与样式，否则运行期报
 * "Failed to resolve component"（Vue 只在控制台警告，界面会缺块）。
 */
import ElButton from 'element-plus/es/components/button/index.mjs'
import ElConfigProvider from 'element-plus/es/components/config-provider/index.mjs'
import ElDialog from 'element-plus/es/components/dialog/index.mjs'
import ElDrawer from 'element-plus/es/components/drawer/index.mjs'
// 子组件（ElDropdownItem/ElDropdownMenu、ElFormItem、ElOption、ElTableColumn）
// 没有独立入口，随父组件模块一起导出
import ElDropdown, { ElDropdownItem, ElDropdownMenu } from 'element-plus/es/components/dropdown/index.mjs'
import ElForm, { ElFormItem } from 'element-plus/es/components/form/index.mjs'
import ElIcon from 'element-plus/es/components/icon/index.mjs'
import ElInput from 'element-plus/es/components/input/index.mjs'
import ElPagination from 'element-plus/es/components/pagination/index.mjs'
import ElProgress from 'element-plus/es/components/progress/index.mjs'
import ElSelect, { ElOption } from 'element-plus/es/components/select/index.mjs'
import ElSwitch from 'element-plus/es/components/switch/index.mjs'
import ElTable, { ElTableColumn } from 'element-plus/es/components/table/index.mjs'
import ElTag from 'element-plus/es/components/tag/index.mjs'
import ElTooltip from 'element-plus/es/components/tooltip/index.mjs'
import ElUpload from 'element-plus/es/components/upload/index.mjs'
import { ElLoading } from 'element-plus/es/components/loading/index.mjs'

import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/config-provider/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import 'element-plus/es/components/drawer/style/css.mjs'
import 'element-plus/es/components/dropdown/style/css.mjs'
import 'element-plus/es/components/dropdown-item/style/css.mjs'
import 'element-plus/es/components/dropdown-menu/style/css.mjs'
import 'element-plus/es/components/form/style/css.mjs'
import 'element-plus/es/components/form-item/style/css.mjs'
import 'element-plus/es/components/icon/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/pagination/style/css.mjs'
import 'element-plus/es/components/progress/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/switch/style/css.mjs'
import 'element-plus/es/components/table/style/css.mjs'
import 'element-plus/es/components/table-column/style/css.mjs'
import 'element-plus/es/components/tag/style/css.mjs'
import 'element-plus/es/components/tooltip/style/css.mjs'
import 'element-plus/es/components/upload/style/css.mjs'
import 'element-plus/es/components/loading/style/css.mjs'
import 'element-plus/es/components/message-box/style/css.mjs'

export const COMPONENTS = [
  ElButton, ElConfigProvider, ElDialog, ElDrawer, ElDropdown, ElDropdownItem, ElDropdownMenu,
  ElForm, ElFormItem, ElIcon, ElInput, ElOption, ElPagination, ElProgress, ElSelect, ElSwitch,
  ElTable, ElTableColumn, ElTag, ElTooltip, ElUpload,
]

export { ElLoading }

/** 统一注册入口：组件 + v-loading 指令。 */
export function installElement(app) {
  COMPONENTS.forEach((component) => app.component(component.name, component))
  app.use(ElLoading)
}
