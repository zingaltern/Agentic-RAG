/**
 * 13.11.2：BASE_URL 单一常量文件 —— 后端地址策略唯一入口。
 * Web（dev/prod）同源走 ''（vite proxy / FastAPI 托管）；桌面端（W8）经 VITE_API_BASE 注入。
 */
export const BASE_URL = import.meta.env.VITE_API_BASE || ''
