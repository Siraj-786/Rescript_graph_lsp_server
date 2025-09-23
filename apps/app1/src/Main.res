// src/Main.res



// Import React and JSX from the rescript/react library
@react.component
let make = () => {
  // Define some user data using a let binding
  let myId = 1
  let myName = "Alice"
  let myEmail = "alice@example.com"

  // Use a function from Utils.res to create a user record
  let user1 = Utils.makeUser(myId, myName, myEmail)

  // Use another function from Utils.res to get the email
  let userEmail = Utils.getEmail(user1)

  // Use a module-scoped function from Data.res
  let status = Utils.loginUser(true)
  let statusMessage = Data.Request.fetchData(status)

  // Throw a custom exception from Data.res
  let _ = try {
    if myName == "Alice" {
      raise(Data.NetworkError("User 'Alice' is not allowed"))
    }
  } catch {
  | Data.NetworkError(msg) => Js.log("Caught a network error: " ++ msg)
  | _ => ()
  }

  // JSX syntax for rendering a simple UI element
  <div>
    <h1> {React.string("User Profile")} </h1>
    <p> {React.string("Email: " ++ userEmail)} </p>
    <p> {React.string("Status: " ++ statusMessage)} </p>
  </div>
}