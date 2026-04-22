package demo;

public class LegacyConfig {
    public boolean isDebug(AppConfig config) {
        // 将常量前置到equals()左侧，安全处理config.getMode()可能返回null的情况
        return "debug".equals(config.getMode());
    }
}
