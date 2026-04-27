package demo;

public class UserService {
    public boolean isAdmin(User user) {
        // BUG: unsafe-equals-order
        return user.getName().equals("admin");
    }
}
